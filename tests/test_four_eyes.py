"""REQUIRE_FOUR_EYES (phase 7.8, decision D2; migration 0020).

The setting is an append-only, versioned table (amaze_settings); the latest row wins and no row means four-eyes
ON. The database guard "the same person cannot decide two steps of one proposal" is a trigger that reads it, so
the repository and the guard see one value. Under both settings every other guard holds: the second role must
be held, the owner alone approves a go-live, configuration needs an approved proposal.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from core.repositories.errors import NotAnApprover, SameApproverTwice
from core.repositories.proposals import ProposalRepository
from core.repositories.configuration import RuleRepository
from core.repositories.settings import FourEyesRepository
from ports.config import Settings
from ports.identity.dev import DevIdentity
from tests.configured import a_registered_feed, go_live, rule_proposal
from tests.conftest import dev_user_for, fresh_schema, replay_ddl, running_app

STEWARD_THEN_CHECKER = ("data_steward", "checker")
INSERT_APPROVAL = ("INSERT INTO approvals (proposal_id, step, role, decided_by, decision) "
                   "VALUES (:p, :step, :role, :by, 'approved')")


@pytest.fixture
def operator():
    user = DevIdentity().get_user("dev-operator")
    assert user is not None
    return user


@pytest.fixture
def four_eyes(db_session):
    return FourEyesRepository(db_session)


def turn_off(db_session):
    FourEyesRepository(db_session).set(False, set_by="dev-operator", reason="synthetic test")


def two_step(make_proposal):
    return make_proposal(kind="rule", roles=STEWARD_THEN_CHECKER)


# ---------------------------------------------------------------- no row: four-eyes is on


def test_no_row_means_on(db_session, four_eyes):
    assert db_session.execute(text("SELECT amaze_four_eyes()")).scalar_one() is True
    assert four_eyes.current() is True
    assert four_eyes.history() == []


def test_the_operator_holds_every_role_but_requester(operator):
    assert "requester" not in operator.roles
    assert operator.roles == ["backend_team", "data_owner", "data_steward", "checker", "glossary_owner",
                              "operations"]


def test_on_the_same_person_cannot_decide_two_steps(db_session, make_proposal, operator):
    repo = ProposalRepository(db_session)
    proposal = two_step(make_proposal)
    repo.record_decision(proposal.id, user=operator, decision="approved")
    with pytest.raises(SameApproverTwice):
        repo.record_decision(proposal.id, user=operator, decision="approved")
    assert repo.get(proposal.id).status == "pending"


def test_on_the_database_refuses_a_second_row_of_the_same_person(db_session, make_proposal):
    proposal = two_step(make_proposal)
    db_session.execute(text(INSERT_APPROVAL), {"p": proposal.id, "step": 1, "role": "data_steward", "by": "dev-x"})
    with pytest.raises(IntegrityError, match="dev-x"):
        with db_session.begin_nested():
            db_session.execute(text(INSERT_APPROVAL), {"p": proposal.id, "step": 2, "role": "checker", "by": "dev-x"})


def test_on_approve_my_steps_decides_exactly_one_step(db_session, make_proposal, operator):
    repo = ProposalRepository(db_session)
    proposal = two_step(make_proposal)
    done = repo.approve_my_steps(proposal.id, user=operator)
    assert [(a.step, a.role) for a in done] == [(1, "data_steward")]
    assert repo.get(proposal.id).status == "pending"


# ---------------------------------------------------------------- a false row: one operator may hold every role


def test_off_the_same_person_approves_both_steps(db_session, make_proposal, operator):
    turn_off(db_session)
    repo = ProposalRepository(db_session)
    proposal = two_step(make_proposal)
    repo.record_decision(proposal.id, user=operator, decision="approved")
    repo.record_decision(proposal.id, user=operator, decision="approved")
    assert repo.get(proposal.id).status == "approved"
    assert [(a.step, a.role, a.decided_by) for a in repo.approvals(proposal.id)] == [
        (1, "data_steward", "dev-operator"), (2, "checker", "dev-operator")]


def test_off_the_configuration_guard_accepts_the_one_persons_approval(db_session, operator):
    turn_off(db_session)
    feed = a_registered_feed(db_session)
    proposal = rule_proposal(db_session, feed)
    ProposalRepository(db_session).approve_my_steps(proposal.id, user=operator)
    rule = RuleRepository(db_session).add_version(feed_id=feed, rule_key=proposal.payload["rule_key"],
                                                  sql=proposal.payload["sql"],
                                                  description=proposal.payload["description"],
                                                  proposal_id=proposal.id)
    assert rule.version == 1


def test_off_approve_my_steps_writes_one_row_per_role_in_order(db_session, make_proposal, operator):
    turn_off(db_session)
    repo = ProposalRepository(db_session)
    proposal = two_step(make_proposal)
    done = repo.approve_my_steps(proposal.id, user=operator, note="all mine")
    assert [(a.step, a.role, a.decision, a.note) for a in done] == [
        (1, "data_steward", "approved", "all mine"), (2, "checker", "approved", "all mine")]
    assert db_session.execute(text("SELECT count(*) FROM approvals WHERE proposal_id = :p"),
                              {"p": proposal.id}).scalar_one() == 2
    assert repo.get(proposal.id).status == "approved"


def test_off_approve_my_steps_stops_at_the_first_role_the_user_lacks(db_session, make_proposal):
    turn_off(db_session)
    repo = ProposalRepository(db_session)
    proposal = two_step(make_proposal)
    done = repo.approve_my_steps(proposal.id, user=dev_user_for("data_steward"))
    assert [(a.step, a.role) for a in done] == [(1, "data_steward")]
    assert repo.get(proposal.id).status == "pending"


def test_off_the_first_role_is_still_needed(db_session, make_proposal):
    turn_off(db_session)
    proposal = two_step(make_proposal)
    with pytest.raises(NotAnApprover):
        ProposalRepository(db_session).approve_my_steps(proposal.id, user=dev_user_for("checker"))


def test_off_the_go_live_is_still_the_named_owners(db_session):
    """The owner-only go-live guard (0018, G6) holds under a false row: the operator is not the feed's owner."""
    turn_off(db_session)
    feed = a_registered_feed(db_session)
    with pytest.raises(DBAPIError, match="go-live: approved_by"):
        with db_session.begin_nested():
            go_live(db_session, feed, owner="dev-operator")
    assert go_live(db_session, feed).approved_by == "dev-data-owner"  # the named owner still passes


def test_off_a_rejection_still_ends_the_proposal(db_session, make_proposal, operator):
    turn_off(db_session)
    repo = ProposalRepository(db_session)
    proposal = two_step(make_proposal)
    repo.record_decision(proposal.id, user=operator, decision="rejected", note="no")
    assert repo.get(proposal.id).status == "rejected"


# ---------------------------------------------------------------- back to true


def test_back_to_on_it_is_refused_again_at_both_levels(db_session, make_proposal, four_eyes, operator):
    turn_off(db_session)
    assert four_eyes.set(True, set_by="dev-operator", reason="synthetic test: back on") is True
    assert four_eyes.current() is True
    repo = ProposalRepository(db_session)
    proposal = two_step(make_proposal)
    repo.record_decision(proposal.id, user=operator, decision="approved")
    with pytest.raises(SameApproverTwice):
        repo.record_decision(proposal.id, user=operator, decision="approved")
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(text(INSERT_APPROVAL), {"p": proposal.id, "step": 2, "role": "checker",
                                                       "by": "dev-operator"})


# ---------------------------------------------------------------- the settings table


def test_set_writes_only_on_a_change(db_session, four_eyes):
    assert four_eyes.set(True, set_by="api-startup", reason="same as the default") is False
    assert four_eyes.history() == []
    assert four_eyes.set(False, set_by="api-startup", reason="first") is True
    assert four_eyes.set(False, set_by="api-startup", reason="again") is False
    assert four_eyes.set(True, set_by="api-startup", reason="second") is True
    assert [(h.version, h.require_four_eyes) for h in four_eyes.history()] == [(1, False), (2, True)]


def test_a_row_needs_a_person_and_a_reason(db_session, four_eyes):
    for by, reason in (("", "r"), ("  ", "r"), ("p", ""), ("p", "   ")):
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                db_session.execute(text("INSERT INTO amaze_settings (version, require_four_eyes, set_by, reason) "
                                        "VALUES (1, false, :by, :reason)"), {"by": by, "reason": reason})


def test_the_version_is_unique_and_positive(db_session, four_eyes):
    four_eyes.set(False, set_by="p", reason="r")
    insert = text("INSERT INTO amaze_settings (version, require_four_eyes, set_by, reason) VALUES (:v, true, 'p', 'r')")
    for version in (1, 0):
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                db_session.execute(insert, {"v": version})


@pytest.mark.parametrize("statement", ["UPDATE amaze_settings SET reason = 'changed'", "DELETE FROM amaze_settings",
                                       "TRUNCATE amaze_settings"])
def test_the_settings_are_append_only(db_session, four_eyes, statement):
    four_eyes.set(False, set_by="p", reason="r")
    with pytest.raises(DBAPIError, match="amaze_settings is append-only"):
        with db_session.begin_nested():
            db_session.execute(text(statement))


# ---------------------------------------------------------------- the API


@pytest.fixture
async def api(db_session, migrated_engine):
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None), session=db_session) as client:
        yield client


OPERATOR = {"X-Dev-User": "dev-operator"}


async def decide(client, proposal_id, headers=OPERATOR):
    return await client.post(f"/api/amaze/proposals/{proposal_id}/decision", headers=headers,
                             json={"decision": "approved"})


@pytest.mark.anyio
async def test_the_endpoint_approves_a_two_role_rule_in_one_call_when_off(api, db_session):
    turn_off(db_session)
    feed = a_registered_feed(db_session)
    proposal = rule_proposal(db_session, feed)
    response = await decide(api, proposal.id)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["rule"]["version"] == 1  # RuleStored
    assert response.json()["proposal"]["status"] == "approved"
    assert len(RuleRepository(db_session).current_for_feed(feed)) == 1


@pytest.mark.anyio
async def test_the_endpoint_refuses_the_same_person_twice_when_on(api, db_session):
    feed = a_registered_feed(db_session)
    proposal = rule_proposal(db_session, feed)
    first = await decide(api, proposal.id)
    assert first.status_code == 200, first.text
    assert "rule" not in first.json()["result"]  # RuleAwaitsChecker
    second = await decide(api, proposal.id)
    assert second.status_code == 409, second.text
    assert ProposalRepository(db_session).get(proposal.id).status == "pending"


@pytest.mark.anyio
async def test_the_endpoint_still_needs_a_second_person_when_on(api, db_session):
    feed = a_registered_feed(db_session)
    proposal = rule_proposal(db_session, feed)
    assert (await decide(api, proposal.id, {"X-Dev-User": "dev-data-steward"})).status_code == 200
    done = await decide(api, proposal.id, {"X-Dev-User": "dev-checker"})
    assert done.json()["result"]["rule"]["version"] == 1


# ---------------------------------------------------------------- start-up and health (real commits, own schema)


@pytest.fixture
def own_schema(test_database_url, offline_ddl):
    with fresh_schema(test_database_url, "four_eyes") as (engine, _url, _schema):
        replay_ddl(engine, offline_ddl)
        yield engine


@pytest.mark.anyio
async def test_health_shows_the_setting_from_the_database(own_schema):
    async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
        assert (await client.get("/api/health")).json()["four_eyes"] is True
        with own_schema.begin() as connection:
            connection.execute(text("INSERT INTO amaze_settings (version, require_four_eyes, set_by, reason) "
                                    "VALUES (1, false, 'p', 'r')"))
        assert (await client.get("/api/health")).json()["four_eyes"] is False


@pytest.mark.anyio
async def test_start_up_writes_the_setting_row(own_schema):
    from apps.api.main import default_four_eyes_sync

    def committed(sql):
        with own_schema.connect() as connection:
            return connection.execute(text(sql)).all()

    assert committed("SELECT count(*) FROM amaze_settings") == [(0,)]
    async with running_app(engine=own_schema, settings=Settings(_env_file=None, require_four_eyes=False),
                           four_eyes_sync=default_four_eyes_sync):
        pass
    assert committed("SELECT version, require_four_eyes, set_by, reason FROM amaze_settings") == [
        (1, False, "api-startup", "REQUIRE_FOUR_EYES at start-up")]
