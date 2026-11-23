"""Migration 0018's go-live guard (phase 6 plan, section 6: G6), by raw SQL.

A feed_go_lives row is the owner's approval of one configuration (mapping ids and rule ids) and one test load.
Every column is written with raw SQL, so only the database decides. Each refusal goes through answered_by():
refused with the guard's message, and with only that branch removed (inside a savepoint rolled back at once)
no longer refused with that message. The summary proposals, loads and stages the guard needs are written with
the helpers of test_db_load_guards.py and the proposal repository. Each test starts with a refusal, so against
0017 (no table) it fails.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from tests.configured import a_registered_feed, logged_call
from tests.conftest import dev_user_for
from tests.onboarded import registered_feed
from tests.test_db_load_guards import loaded_load, new_load
from tests.test_db_onboarding_guards import _branch_off, answered_by

GO_LIVE = ("INSERT INTO feed_go_lives (feed_id, version, proposal_id, approved_by, mapping_ids, rule_ids, "
           "test_load_id, hypercare_until, code_version_id) VALUES (:feed, :version, :proposal, :by, "
           "CAST(:mappings AS uuid[]), CAST(:rules AS uuid[]), :load, :until, :code) RETURNING id")


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def world(s):
    """A registered feed (owner dev-data-owner), a configuration to pin and a loaded test load."""
    feed = a_registered_feed(s, "syn_go_live_guards")
    return SimpleNamespace(feed=feed, mappings=[uuid.uuid4(), uuid.uuid4()], rules=[uuid.uuid4()],
                           load=loaded_load(s, feed), s=s)


def summary_payload(w, **over):
    return {"mapping_ids": [str(i) for i in w.mappings], "rule_ids": [str(i) for i in w.rules],
            "test_load_id": str(w.load), "summary": "Synthetic.", "highlights": [], "risks": [], **over}


def summary_proposal(s, w, *, feed=None, decision="approved", kind="review_summary", payload=None):
    """A review_summary proposal of the real shape on a logged call; decided by dev-data-owner if asked. A
    rule proposal (kind='rule') is for the wrong-kind case: its steps are the steward's and the checker's."""
    roles = ["data_owner"] if kind == "review_summary" else ["data_steward", "checker"]
    payload = summary_payload(w) if payload is None else payload
    repo = ProposalRepository(s)
    proposal = repo.create(proposal_log_id=logged_call(s, "ReviewSummariserAgent", "review_summariser").id,
                           kind=kind, payload=payload, autonomy="A1" if kind == "review_summary" else "A2",
                           required_approver_roles=roles, feed_id=feed or w.feed)
    if decision == "approved":
        for role in roles:
            repo.record_decision(proposal.id, user=dev_user_for(role), decision="approved")
    elif decision == "rejected":
        repo.record_decision(proposal.id, user=dev_user_for(roles[0]), decision="rejected", note="Synthetic.")
    return repo.get(proposal.id)


EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # the hash of no files


def a_code_version(s, p) -> uuid.UUID:
    """0030: every new go-live pins a code version of its feed with the same owner, load and ids (empty files)."""
    version = s.scalar(text("SELECT COALESCE(max(version), 0) + 1 FROM code_versions WHERE feed_id = :f"), {"f": p["feed"]})
    return s.execute(text(
        "INSERT INTO code_versions (feed_id, version, sha256, files, mapping_ids, rule_ids, transformation_ids, "
        "test_load_id, test_load_result, approved_by) VALUES (:feed, :v, :h, CAST('{}' AS jsonb), CAST(:mappings AS uuid[]), "
        "CAST(:rules AS uuid[]), CAST(:tr AS uuid[]), :load, CAST('{}' AS jsonb), :by) RETURNING id"),
        {"feed": p["feed"], "v": version, "h": EMPTY_SHA256, "mappings": p["mappings"], "rules": p["rules"],
         "tr": p.get("transformations", []), "load": p["load"], "by": p["by"]}).scalar_one()


def go_live_params(w, proposal, **over):
    params = {"feed": w.feed, "version": 1, "proposal": proposal.id if proposal is not None else None,
              "by": "dev-data-owner", "mappings": w.mappings, "rules": w.rules, "load": w.load,
              "until": date.today() + timedelta(days=10), **over}
    return {**params, "code": a_code_version(w.s, params)}


def guard(message):
    return _branch_off("amaze_guard_feed_go_live", message)


# ---------------------------------------------------------------- an approved review_summary of this feed

NOT_APPROVED = guard("go-live: proposal % is not an approved review_summary of feed %")


@pytest.mark.parametrize("case", ["pending", "rejected", "a rule proposal", "another feed's summary"])
def test_a_go_live_needs_an_approved_review_summary_of_its_feed(s, world, case):
    w = world
    if case == "pending":
        proposal = summary_proposal(s, w, decision=None)
    elif case == "rejected":
        proposal = summary_proposal(s, w, decision="rejected")
    elif case == "a rule proposal":
        proposal = summary_proposal(s, w, kind="rule", payload={"rule_key": "k", "sql": "SELECT 1"})
    else:
        proposal = summary_proposal(s, w, feed=a_registered_feed(s, "syn_go_live_other"))
    answered_by(s, "is not an approved review_summary", GO_LIVE, go_live_params(w, proposal), NOT_APPROVED)


# ---------------------------------------------------------------- the owner decided it


def test_the_go_live_names_the_person_who_decided_the_summary(s, world):
    proposal = summary_proposal(s, world)  # decided by dev-data-owner
    answered_by(s, "did not decide proposal", GO_LIVE, go_live_params(world, proposal, by="dev-backend-team"),
                guard("go-live: approved_by % did not decide proposal % as data_owner"))


def test_the_go_live_is_approved_by_the_owner_named_at_registration(s, world):
    """dev-data-owner holds data_owner and decided the summary, but the feed's registration names another
    owner (phase 4 note 4: the owner's first decision is the named owner's)."""
    other = FeedRepository(s).create_candidate(code="syn_go_live_named", name="Named owner").id
    registered_feed(s, other, owner_id="dev-someone-else")
    w = SimpleNamespace(feed=other, mappings=world.mappings, rules=world.rules, load=loaded_load(s, other), s=s)
    proposal = summary_proposal(s, w)
    answered_by(s, "is not the owner of feed", GO_LIVE, go_live_params(w, proposal),
                guard("go-live: approved_by % is not the owner of feed %"))


# ---------------------------------------------------------------- it pins the proposal's configuration and test load


@pytest.mark.parametrize("case", ["other ids", "no key"])
def test_the_go_live_pins_the_mapping_ids_of_the_summary(s, world, case):
    w = world
    if case == "other ids":
        proposal, params = summary_proposal(s, w), go_live_params(w, None, mappings=[uuid.uuid4()])
    else:  # the NULL case: a summary with no mapping_ids key reads as NULL, which must not match the row's
        payload = summary_payload(w)
        del payload["mapping_ids"]
        proposal, params = summary_proposal(s, w, payload=payload), go_live_params(w, None)
    answered_by(s, "mapping_ids are not those", GO_LIVE, {**params, "proposal": proposal.id},
                guard("go-live: mapping_ids are not those of proposal %"))


@pytest.mark.parametrize("case", ["other ids", "no key"])
def test_the_go_live_pins_the_rule_ids_of_the_summary(s, world, case):
    w = world
    if case == "other ids":
        proposal, params = summary_proposal(s, w), go_live_params(w, None, rules=[uuid.uuid4()])
    else:
        payload = summary_payload(w)
        del payload["rule_ids"]
        proposal, params = summary_proposal(s, w, payload=payload), go_live_params(w, None)
    answered_by(s, "rule_ids are not those", GO_LIVE, {**params, "proposal": proposal.id},
                guard("go-live: rule_ids are not those of proposal %"))


def test_the_ids_are_compared_whatever_their_order(s, world):
    proposal = summary_proposal(s, world)
    s.execute(text(GO_LIVE), go_live_params(world, proposal, mappings=list(reversed(world.mappings))))


@pytest.mark.parametrize("case", ["another load", "no key"])
def test_the_go_live_pins_the_test_load_of_the_summary(s, world, case):
    w = world
    if case == "another load":
        proposal = summary_proposal(s, w)
        params = go_live_params(w, proposal, load=loaded_load(s, w.feed))
    else:
        payload = summary_payload(w)
        del payload["test_load_id"]
        proposal = summary_proposal(s, w, payload=payload)
        params = go_live_params(w, proposal)
    answered_by(s, "test_load_id is not the one", GO_LIVE, params,
                guard("go-live: test_load_id is not the one of proposal %"))


# ---------------------------------------------------------------- the test load is a loaded go_live_test of the feed

NOT_A_TEST_LOAD = guard("go-live: load % is not a loaded go_live_test of feed %")


def test_the_test_load_belongs_to_the_feed(s, world):
    w = world
    w.load = loaded_load(s, a_registered_feed(s, "syn_go_live_other"))
    answered_by(s, "is not a loaded go_live_test", GO_LIVE, go_live_params(w, summary_proposal(s, w)), NOT_A_TEST_LOAD)


def test_the_test_load_has_finished_loaded(s, world):
    w = world
    w.load = new_load(s, w.feed)  # still received
    answered_by(s, "is not a loaded go_live_test", GO_LIVE, go_live_params(w, summary_proposal(s, w)), NOT_A_TEST_LOAD)


def test_the_test_load_is_not_a_scheduled_load(s, world):
    w = world
    first = s.execute(text(GO_LIVE), go_live_params(w, summary_proposal(s, w))).scalar_one()
    w.load = loaded_load(s, w.feed, purpose="scheduled", go_live=first)
    answered_by(s, "is not a loaded go_live_test", GO_LIVE, go_live_params(w, summary_proposal(s, w), version=2),
                NOT_A_TEST_LOAD)


# ---------------------------------------------------------------- 0023: it pins the feed's current transformations

GO_LIVE_T = GO_LIVE.replace("rule_ids, test_load_id", "rule_ids, transformation_ids, test_load_id").replace(
    "CAST(:rules AS uuid[]), :load", "CAST(:rules AS uuid[]), CAST(:transformations AS uuid[]), :load")


def transform_world(s, w):
    """The feed gets a mapped column with two transformation versions; returns (old, current)."""
    from tests.configured import approved_mapping, approved_transformation

    approved_mapping(s, w.feed, "px", "close_price")
    old = approved_transformation(s, w.feed, "px", [{"op": "trim"}])
    return old, approved_transformation(s, w.feed, "px", [{"op": "trim"}, {"op": "cast", "type": "decimal"}])


def t_params(w, proposal, ids, **over):
    return go_live_params(w, proposal, transformations=ids, **over)


def test_a_feed_with_no_transformations_pins_none(s, world):
    proposal = summary_proposal(s, world)
    s.execute(text(GO_LIVE_T), t_params(world, proposal, []))


def test_pinning_the_current_transformations_passes(s, world):
    w = world
    old, current = transform_world(s, w)
    proposal = summary_proposal(s, w, payload=summary_payload(w, transformation_ids=[str(current.id)]))
    s.execute(text(GO_LIVE_T), t_params(w, proposal, [current.id]))


@pytest.mark.parametrize("case", ["none", "an old version", "an extra id", "the current and an extra id"])
def test_a_go_live_pins_exactly_the_current_transformations(s, world, case):
    import uuid

    w = world
    old, current = transform_world(s, w)
    ids = {"none": [], "an old version": [old.id], "an extra id": [uuid.uuid4()],
           "the current and an extra id": [current.id, uuid.uuid4()]}[case]
    proposal = summary_proposal(s, w, payload=summary_payload(w, transformation_ids=[str(i) for i in ids]))
    answered_by(s, "are not the current transformations", GO_LIVE_T, t_params(w, proposal, ids),
                guard("go-live: transformation_ids are not the current transformations of feed %"))


@pytest.mark.parametrize("case", ["other ids", "no key"])
def test_the_go_live_pins_the_transformation_ids_of_the_summary(s, world, case):
    w = world
    old, current = transform_world(s, w)
    payload = summary_payload(w, transformation_ids=[str(old.id)]) if case == "other ids" else summary_payload(w)
    proposal = summary_proposal(s, w, payload=payload)
    answered_by(s, "transformation_ids are not those", GO_LIVE_T, t_params(w, proposal, [current.id]),
                guard("go-live: transformation_ids are not those of proposal %"))


# ---------------------------------------------------------------- the version is the feed's next


def test_the_version_is_the_feeds_next_one(s, world):
    w = world
    first = summary_proposal(s, w)
    answered_by(s, "is not the next one", GO_LIVE, go_live_params(w, first, version=2),
                guard("go-live: version % is not the next one (%)"))
    s.execute(text(GO_LIVE), go_live_params(w, first))
    second = summary_proposal(s, w)
    answered_by(s, "is not the next one", GO_LIVE, go_live_params(w, second, version=1),
                guard("go-live: version % is not the next one (%)"))
    s.execute(text(GO_LIVE), go_live_params(w, second, version=2))


def test_two_go_lives_never_come_from_one_summary(s, world):
    proposal = summary_proposal(s, world)
    s.execute(text(GO_LIVE), go_live_params(world, proposal))
    answered_by(s, "uq_feed_go_lives_proposal_id", GO_LIVE, go_live_params(world, proposal, version=2),
                "ALTER TABLE feed_go_lives DROP CONSTRAINT uq_feed_go_lives_proposal_id")
