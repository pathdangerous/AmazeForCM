"""ProposalRepository: proposals linked to a logged model call; decisions as approvals rows.

Rules (plan, assumption 8): roles decide in the listed order, one decision per step, the same person
cannot decide two steps of one proposal, the first rejection ends it, nothing is decided after that.
"""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.repositories.errors import (
    NotAnApprover,
    ProposalAlreadyDecided,
    ProposalLogEntryNotFound,
    ProposalNotApproved,
    ProposalNotFound,
    SameApproverTwice,
    WrongProposalKind,
)
from core.repositories.proposals import ProposalRepository
from ports.identity.base import User
from tests.conftest import dev_user_for

STEWARD_THEN_CHECKER = ("data_steward", "checker")


@pytest.fixture
def repo(db_session):
    return ProposalRepository(db_session)


def test_create_is_pending_and_linked_to_its_logged_call(repo, make_proposal, logged_call):
    proposal = make_proposal(kind="rule", roles=STEWARD_THEN_CHECKER, payload={"sql": "SELECT 1"})
    assert proposal.status == "pending"
    assert proposal.decided_at is None
    assert proposal.proposal_log_id == logged_call.id
    assert proposal.required_approver_roles == list(STEWARD_THEN_CHECKER)
    assert repo.get(proposal.id) == proposal


def test_create_needs_an_existing_logged_call(repo):
    with pytest.raises(ProposalLogEntryNotFound):
        repo.create(proposal_log_id=uuid.uuid4(), kind="mapping", payload={}, autonomy="A2",
                    required_approver_roles=["data_steward"])


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "discovery"},  # the read-only DiscoveryAssistant makes no proposals
        {"autonomy": "A3"},  # A3 is not used in the PoC
        {"required_approver_roles": []},
        {"required_approver_roles": ["superuser"]},
    ],
)
def test_create_rejects_invalid_input(repo, logged_call, bad):
    fields = {"kind": "mapping", "payload": {}, "autonomy": "A2", "required_approver_roles": ["data_steward"]}
    with pytest.raises(ValidationError):
        repo.create(proposal_log_id=logged_call.id, **{**fields, **bad})


def test_single_step_approval(repo, make_proposal):
    proposal = make_proposal(roles=("glossary_owner",), kind="glossary_element")
    approval = repo.record_decision(proposal.id, user=dev_user_for("glossary_owner"), decision="approved", note="ok")
    assert (approval.step, approval.role, approval.decided_by, approval.decision, approval.note) == (
        1, "glossary_owner", "dev-glossary-owner", "approved", "ok",
    )
    decided = repo.get(proposal.id)
    assert decided.status == "approved"
    assert decided.decided_at is not None


def test_steward_then_checker(repo, make_proposal):
    proposal = make_proposal(kind="rule", roles=STEWARD_THEN_CHECKER)
    repo.record_decision(proposal.id, user=dev_user_for("data_steward"), decision="approved")
    assert repo.get(proposal.id).status == "pending"
    repo.record_decision(proposal.id, user=dev_user_for("checker"), decision="approved")
    assert repo.get(proposal.id).status == "approved"
    assert [(a.step, a.role) for a in repo.approvals(proposal.id)] == [(1, "data_steward"), (2, "checker")]


def test_wrong_role_for_the_step(repo, make_proposal):
    proposal = make_proposal(kind="rule", roles=STEWARD_THEN_CHECKER)
    with pytest.raises(NotAnApprover, match="data_steward"):
        repo.record_decision(proposal.id, user=dev_user_for("checker"), decision="approved")
    assert repo.approvals(proposal.id) == []


def test_same_person_cannot_decide_two_steps(repo, make_proposal):
    both = User(id="dev-both", display_name="Synthetic Both", email="both@example.test",
                roles=["data_steward", "checker"])
    proposal = make_proposal(kind="rule", roles=STEWARD_THEN_CHECKER)
    repo.record_decision(proposal.id, user=both, decision="approved")
    with pytest.raises(SameApproverTwice):
        repo.record_decision(proposal.id, user=both, decision="approved")
    assert repo.get(proposal.id).status == "pending"


def test_first_rejection_ends_it(repo, make_proposal):
    proposal = make_proposal(kind="rule", roles=STEWARD_THEN_CHECKER)
    repo.record_decision(proposal.id, user=dev_user_for("data_steward"), decision="rejected", note="wrong column")
    assert repo.get(proposal.id).status == "rejected"
    with pytest.raises(ProposalAlreadyDecided):
        repo.record_decision(proposal.id, user=dev_user_for("checker"), decision="approved")


def test_nothing_after_approval(repo, make_proposal):
    proposal = make_proposal(roles=("data_steward",))
    repo.record_decision(proposal.id, user=dev_user_for("data_steward"), decision="approved")
    with pytest.raises(ProposalAlreadyDecided):
        repo.record_decision(proposal.id, user=dev_user_for("data_steward"), decision="rejected")
    assert [a.decision for a in repo.approvals(proposal.id)] == ["approved"]


def test_unknown_proposal(repo):
    with pytest.raises(ProposalNotFound):
        repo.record_decision(uuid.uuid4(), user=dev_user_for("checker"), decision="approved")
    with pytest.raises(ProposalNotFound):
        repo.get(uuid.uuid4())


def test_invalid_decision_value(repo, make_proposal):
    proposal = make_proposal()
    with pytest.raises(ValueError):
        repo.record_decision(proposal.id, user=dev_user_for("data_steward"), decision="maybe")


def test_list_is_newest_first_within_one_transaction(repo, make_proposal):
    ids = [make_proposal().id for _ in range(5)]
    assert [p.id for p in repo.list()] == list(reversed(ids))


def test_list_filters(repo, make_proposal, approved_proposal):
    pending = make_proposal(kind="mapping")
    approved = approved_proposal(kind="rule", roles=STEWARD_THEN_CHECKER)
    assert [p.id for p in repo.list(status="pending")] == [pending.id]
    assert [p.id for p in repo.list(status="approved")] == [approved.id]
    assert [p.id for p in repo.list(kind="rule")] == [approved.id]
    assert {p.id for p in repo.list()} == {pending.id, approved.id}


def test_audit_shows_rule_4_in_one_place(repo, approved_proposal, logged_call):
    proposal = approved_proposal(kind="rule", roles=STEWARD_THEN_CHECKER)
    audit = repo.audit(proposal.id)
    assert audit["agent_name"] == "SmokeAgent"
    assert (audit["provider"], audit["model"], audit["model_version"]) == ("mock", "mock", "1")
    assert (audit["prompt_id"], audit["prompt_version"]) == ("phase0_smoke", "v1")
    assert audit["input_ref"] == "blob://inputs/1"
    assert audit["output"] == logged_call.output
    assert audit["status"] == "approved"
    assert [(d["step"], d["role"], d["decided_by"], d["decision"]) for d in audit["decisions"]] == [
        (1, "data_steward", "dev-data-steward", "approved"),
        (2, "checker", "dev-checker", "approved"),
    ]


def test_require_approved(repo, make_proposal, approved_proposal):
    with pytest.raises(ProposalNotApproved):
        repo.require_approved(make_proposal(kind="mapping").id, kind="mapping")
    approved = approved_proposal(kind="mapping")
    with pytest.raises(WrongProposalKind):
        repo.require_approved(approved.id, kind="rule")
    assert repo.require_approved(approved.id, kind="mapping").id == approved.id
    with pytest.raises(ProposalNotFound):
        repo.require_approved(uuid.uuid4(), kind="mapping")


# ---- the database enforces the same rules when the repository is bypassed


def test_database_refuses_two_decisions_for_one_step(db_session, make_proposal):
    proposal = make_proposal()
    insert = text(
        "INSERT INTO approvals (proposal_id, step, role, decided_by, decision) "
        "VALUES (:p, 1, 'data_steward', :who, 'approved')"
    )
    db_session.execute(insert, {"p": proposal.id, "who": "dev-data-steward"})
    with pytest.raises(IntegrityError, match="uq_approvals_proposal_id_step"):
        db_session.execute(insert, {"p": proposal.id, "who": "dev-someone-else"})


def test_database_refuses_a_pending_proposal_with_a_decision_time(db_session, make_proposal):
    proposal = make_proposal()
    with pytest.raises(IntegrityError, match="ck_proposals_decided_at_matches_status"):
        db_session.execute(text("UPDATE proposals SET decided_at = now() WHERE id = :id"), {"id": proposal.id})


# ---------------------------------------------------------------- phase 5: proposals of one column of a feed


def test_proposals_of_one_column_and_the_pending_one(repo, db_session):
    from tests.configured import a_registered_feed, mapping_proposal

    feed = a_registered_feed(db_session)
    rejected = mapping_proposal(db_session, feed, "px", ["close_price"], decision="rejected")
    pending = mapping_proposal(db_session, feed, "px", ["mid_price"])
    mapping_proposal(db_session, feed, "qty", ["volume"])
    assert [p.id for p in repo.list_for_feed_column("mapping", feed, "px")] == [rejected.id, pending.id]
    assert [p.id for p in repo.list_for_feed_column("mapping", feed, "px", status="rejected")] == [rejected.id]
    assert repo.pending_for_column("mapping", feed, "px").id == pending.id
    assert repo.pending_for_column("glossary_element", feed, "px") is None
    assert repo.pending_for_column("mapping", feed, "other") is None
