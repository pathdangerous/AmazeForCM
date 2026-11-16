"""Rule 1 in the database: a row in mappings, rules or glossary_elements (except the seed) can only be
inserted for a proposal that is approved, of the matching kind, has one 'approved' decision in
approvals for every role in required_approver_roles, and has no 'rejected' decision (migration 0005).
Raw SQL bypasses the repositories on purpose."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository

STEWARD_THEN_CHECKER = ("data_steward", "checker")


# ---------------------------------------------------------------- 0015: a proposal changes only by its decision


@pytest.mark.parametrize("change", [
    "payload = '{\"recommended_outcome\": \"rejected\"}'", "kind = 'rule'", "autonomy = 'A1'",
    # the fixture's proposal has no links: a new value (the BEFORE trigger answers before the foreign key)
    "required_approver_roles = ARRAY['checker']", "feed_id = gen_random_uuid()", "requirement_id = gen_random_uuid()",
    "created_at = now() - interval '1 day'",
])
def test_a_proposal_changes_only_in_status_and_decided_at(db_session, make_proposal, change):
    from sqlalchemy.exc import DBAPIError

    proposal = make_proposal(payload={"recommended_outcome": "meets"})
    with pytest.raises(DBAPIError, match="only status and decided_at change"):
        with db_session.begin_nested():
            db_session.execute(text(f"UPDATE proposals SET {change} WHERE id = :id"), {"id": proposal.id})


def test_a_proposal_is_never_deleted(db_session, make_proposal):
    from sqlalchemy.exc import DBAPIError

    proposal = make_proposal()
    with pytest.raises(DBAPIError, match="proposals: DELETE refused"):
        with db_session.begin_nested():
            db_session.execute(text("DELETE FROM proposals WHERE id = :id"), {"id": proposal.id})


def test_the_decision_path_still_decides(db_session, make_proposal):
    """record_decision sets status and decided_at, the only columns it writes; both outcomes still work."""
    from core.repositories.proposals import ProposalRepository
    from tests.conftest import dev_user_for

    repo = ProposalRepository(db_session)
    approved, rejected = make_proposal(), make_proposal()
    repo.record_decision(approved.id, user=dev_user_for("data_steward"), decision="approved")
    repo.record_decision(rejected.id, user=dev_user_for("data_steward"), decision="rejected")
    db_session.expire_all()
    assert [(p.status, p.decided_at is not None) for p in (repo.get(approved.id), repo.get(rejected.id))] == [
        ("approved", True), ("rejected", True)]

# table -> (proposal kind, roles that approve it, raw insert using :feed and :proposal)
TABLES = {
    "mappings": (
        "mapping", ("data_steward",),
        "INSERT INTO mappings (feed_id, source_field, element_key, version, proposal_id) "
        "VALUES (:feed, 'GuardField', 'close_price', 1, :proposal)",
    ),
    "rules": (
        "rule", STEWARD_THEN_CHECKER,
        "INSERT INTO rules (feed_id, rule_key, version, sql, description, proposal_id) "
        "VALUES (:feed, 'guard_rule', 1, 'SELECT 1', 'Synthetic.', :proposal)",
    ),
    "glossary_elements": (
        "glossary_element", ("glossary_owner",),
        "INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, created_by, proposal_id) "
        "VALUES ('guard_element', 1, 'Guard', 'pricing', 'string', 'Synthetic.', 'dev-glossary-owner', :proposal)",
    ),
}
OTHER_KIND = {"mapping": ("rule", STEWARD_THEN_CHECKER), "rule": ("mapping", ("data_steward",)),
              "glossary_element": ("mapping", ("data_steward",))}
# From 0016 an accepted row must also match its proposal (trg_..._b_...; test_db_configuration_guards.py):
# the proposal of the accepted case has the real payload and, for a mapping, the feed.
REAL_PAYLOAD = {
    "mapping": {"source_field": "GuardField", "column_type": None, "candidates": ["close_price"],
                "recommended": "close_price", "reason": "Synthetic."},
    "rule": {"note": "synthetic"},
    "glossary_element": {"source_field": "guard", "outcome": "proposed", "recommended": 0, "reason": "Synthetic.",
                         "run_id": "00000000-0000-0000-0000-000000000000", "options": [
                             {"kind": "new_element", "key": "guard_element", "alias": None, "element": {
                                 "key": "guard_element", "name": "Guard", "domain": "pricing", "data_type": "string",
                                 "definition": "Synthetic.", "aliases": []}}]},
}


@pytest.fixture
def feed(db_session):
    from tests.onboarded import registered_feed

    GlossaryRepository(db_session).add_seed_element(
        key="close_price", name="Close price", domain="pricing", data_type="decimal",
        definition="Synthetic definition.", aliases=[],
    )
    return registered_feed(db_session, FeedRepository(db_session).create_candidate(code="guard_feed",
                                                                                   name="Guard feed").id)


def _insert(db_session, table, feed, proposal_id):
    db_session.execute(text(TABLES[table][2]), {"feed": feed.id, "proposal": proposal_id})


@pytest.mark.parametrize("table", sorted(TABLES))
def test_accepts_a_properly_approved_proposal(db_session, feed, approved_proposal, table):
    kind, roles, _ = TABLES[table]
    links = {"feed_id": feed.id} if kind == "mapping" else {}
    _insert(db_session, table, feed, approved_proposal(kind=kind, roles=roles, payload=REAL_PAYLOAD[kind], **links).id)
    assert db_session.execute(text(f"SELECT count(*) FROM {table} WHERE proposal_id IS NOT NULL")).scalar_one() == 1


@pytest.mark.parametrize("table", sorted(TABLES))
def test_refuses_a_pending_proposal(db_session, feed, make_proposal, table):
    kind, roles, _ = TABLES[table]
    with pytest.raises(IntegrityError, match="is pending, not approved"):
        _insert(db_session, table, feed, make_proposal(kind=kind, roles=roles).id)


@pytest.mark.parametrize("table", sorted(TABLES))
def test_refuses_an_approved_proposal_of_another_kind(db_session, feed, approved_proposal, table):
    kind, _, _ = TABLES[table]
    other_kind, other_roles = OTHER_KIND[kind]
    with pytest.raises(IntegrityError, match=f"is a {other_kind} proposal, not {kind}"):
        _insert(db_session, table, feed, approved_proposal(kind=other_kind, roles=other_roles).id)


@pytest.mark.parametrize("table", sorted(TABLES))
def test_refuses_a_proposal_marked_approved_without_an_approval(db_session, feed, make_proposal, table):
    """A status forged by hand (UPDATE proposals SET status = 'approved') is not enough."""
    kind, roles, _ = TABLES[table]
    proposal = make_proposal(kind=kind, roles=roles)
    db_session.execute(text("UPDATE proposals SET status = 'approved', decided_at = now() WHERE id = :id"),
                       {"id": proposal.id})
    with pytest.raises(IntegrityError, match="has no approved decision"):
        _insert(db_session, table, feed, proposal.id)


def _force_approved(db_session, proposal_id):
    db_session.execute(text("UPDATE proposals SET status = 'approved', decided_at = now() WHERE id = :id"),
                       {"id": proposal_id})


@pytest.mark.parametrize("table", sorted(TABLES))
def test_refuses_a_two_step_proposal_forced_approved_after_step_one(db_session, feed, make_proposal, table):
    """Step 1 approved, step 2 never decided, status forged to 'approved': every required role must
    have its own 'approved' row."""
    from core.repositories.proposals import ProposalRepository
    from tests.conftest import dev_user_for

    kind, roles, _ = TABLES[table]
    proposal = make_proposal(kind=kind, roles=(roles[0], "operations"))
    ProposalRepository(db_session).record_decision(proposal.id, user=dev_user_for(roles[0]), decision="approved")
    _force_approved(db_session, proposal.id)
    with pytest.raises(IntegrityError, match="has no approved decision from role operations"):
        _insert(db_session, table, feed, proposal.id)


@pytest.mark.parametrize("table", sorted(TABLES))
def test_refuses_a_proposal_rejected_at_step_two_and_forced_approved(db_session, feed, make_proposal, table):
    from core.repositories.proposals import ProposalRepository
    from tests.conftest import dev_user_for

    kind, roles, _ = TABLES[table]
    proposal = make_proposal(kind=kind, roles=(roles[0], "operations"))
    repo = ProposalRepository(db_session)
    repo.record_decision(proposal.id, user=dev_user_for(roles[0]), decision="approved")
    repo.record_decision(proposal.id, user=dev_user_for("operations"), decision="rejected")
    # From 0016 the forgery itself is refused (phase 5 plan, assumption 34); 0005's "has a rejected
    # decision" branch keeps test_refuses_a_fully_approved_proposal_with_an_extra_rejected_row.
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError, match="a decided proposal never changes again"):
        _force_approved(db_session, proposal.id)


@pytest.mark.parametrize("table", sorted(TABLES))
def test_refuses_a_fully_approved_proposal_with_an_extra_rejected_row(db_session, feed, approved_proposal, table):
    """Every required role approved, then a 'rejected' row added by hand: any rejection blocks it."""
    kind, roles, _ = TABLES[table]
    proposal = approved_proposal(kind=kind, roles=roles)
    db_session.execute(text(
        "INSERT INTO approvals (proposal_id, step, role, decided_by, decision) "
        "VALUES (:id, :step, 'operations', 'dev-operations', 'rejected')"
    ), {"id": proposal.id, "step": len(roles) + 1})
    with pytest.raises(IntegrityError, match="has a rejected decision"):
        _insert(db_session, table, feed, proposal.id)


def test_seed_glossary_element_needs_no_proposal(db_session):
    db_session.execute(text(
        "INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, created_by) "
        "VALUES ('seed_probe', 1, 'Seed probe', 'pricing', 'string', 'Synthetic.', 'seed')"
    ))
    assert db_session.execute(text("SELECT count(*) FROM glossary_elements WHERE key = 'seed_probe'")).scalar_one() == 1


# ---- review fix 3 (migration 0010): a requester-role approval of a requirement's proposal comes from
# that requirement's own requester. Raw SQL bypasses the steps' own check.

APPROVAL = text("INSERT INTO approvals (proposal_id, step, role, decided_by, decision) "
                "VALUES (:p, 1, :role, :by, 'approved')")


@pytest.fixture
def riley_requirement(db_session):
    from core.repositories.requirements import RequirementRepository

    return RequirementRepository(db_session).create(requester_id="dev-requester", title="open price")


def _approve(db_session, proposal_id, by, role="requester"):
    with db_session.begin_nested():
        db_session.execute(APPROVAL, {"p": proposal_id, "role": role, "by": by})


@pytest.mark.parametrize("by", ["dev-other-requester", " ", ""])
def test_a_requester_approval_of_a_requirements_proposal_needs_its_own_requester(db_session, make_proposal,
                                                                                 riley_requirement, by):
    from sqlalchemy.exc import DBAPIError

    proposal = make_proposal(kind="source_list", roles=("requester",), payload={"source_code": "syn_x"},
                             requirement_id=riley_requirement.id)
    with pytest.raises(DBAPIError, match="only the requirement's requester"):
        _approve(db_session, proposal.id, by)
    _approve(db_session, proposal.id, "dev-requester")  # the requirement's own requester


def test_the_requester_check_applies_only_to_requester_approvals_of_a_requirement(db_session, make_proposal,
                                                                                 riley_requirement):
    no_requirement = make_proposal(kind="scope", roles=("requester",), payload={})
    _approve(db_session, no_requirement.id, "dev-anyone")  # no requirement to check against
    steward = make_proposal(kind="mapping", roles=("data_steward",), requirement_id=riley_requirement.id)
    _approve(db_session, steward.id, "dev-data-steward", role="data_steward")
