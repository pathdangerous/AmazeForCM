"""S1 scope (core/steps/s1_scope.py; phase 3 plan, assumptions 27 to 29).

Outcomes (CLAUDE.md): scoped; scope_not_confirmed. The IntentAgent proposes one scope; only the
requirement's own requester approves or rejects it. Run with a stand-in agent, without a provider.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from core.domain.sourcing import IntentOutput
from core.repositories.errors import RequirementNotInStatus
from core.repositories.proposals import ProposalRepository
from core.repositories.requirement_scopes import RequirementScopeRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.tables import ApprovalRow, ProposalLogEntry, ProposalRow
from core.steps.errors import NotTheRequester
from core.steps.s1_scope import ScopeNotConfirmed, Scoped, decide_scope, propose_scope
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from ports.identity.base import User
from tests.conftest import dev_user_for
from tests.stand_in_agents import StandInAgent

CATALOGUE = SourceCatalogue.load(DEFAULT_CATALOGUE)
PUBLIC = {"visibility": "public", "provider": "External data providers",
          "where_to_search": ["exchanges", "market_data_vendors"], "reason": "Nothing says private."}
REQUESTER = dev_user_for("requester")


def scope_agent(output=None, error=None):
    return StandInAgent(name="IntentAgent", output_schema=IntentOutput, output=output or PUBLIC, error=error,
                        proposals=lambda i, o: [{"kind": "scope", "payload": o.model_dump(mode="json"),
                                                 "requirement_id": i.requirement_id}])


@pytest.fixture
def requirement(db_session, glossary):
    def _make(status="defined", **fields):
        fields = {"requester_id": "dev-requester", "title": "open price", "description": "Desk analysis",
                  "element_keys": ["open_price"], **fields}
        req = RequirementRepository(db_session).create(**fields)
        if status != "defined":
            from sqlalchemy import text

            db_session.execute(text("UPDATE requirements SET status = :s WHERE id = :id"), {"s": status, "id": req.id})
            db_session.expire_all()
        return RequirementRepository(db_session).get(req.id)

    return _make


def count(session, table):
    return session.scalar(select(func.count()).select_from(table))


def status(session, req):
    return RequirementRepository(session).get(req.id).status


def test_propose_scope_makes_one_proposal_for_the_requester(db_session, requirement):
    req, agent = requirement(), scope_agent()
    proposed = propose_scope(db_session, requirement_id=req.id, agent=agent, catalogue=CATALOGUE)
    assert (proposed.visibility, proposed.where_to_search) == ("public", ["exchanges", "market_data_vendors"])
    proposal = ProposalRepository(db_session).get(proposed.proposal_id)
    assert (proposal.kind, proposal.required_approver_roles, proposal.status, proposal.requirement_id) == (
        "scope", ["requester"], "pending", req.id)
    assert proposal.proposal_log_id == proposed.log_id
    assert status(db_session, req) == "awaiting_scope_confirmation"
    sent = agent.inputs[0]
    assert (sent.requirement_text, sent.purpose, sent.note, sent.terms) == ("open price", "Desk analysis", None, [])
    assert [(e.key, e.name) for e in sent.elements] == [("open_price", "Open price")]
    assert {(c.channel, c.visibility) for c in sent.channels} == {
        ("exchanges", "public"), ("market_data_vendors", "public"), ("public_websites", "public"),
        ("internal_systems", "private")}


def test_scoped_when_the_requester_confirms(db_session, requirement):
    req = requirement()
    proposed = propose_scope(db_session, requirement_id=req.id, agent=scope_agent(), catalogue=CATALOGUE)
    result = decide_scope(db_session, requirement_id=req.id, user=REQUESTER, decision="approved")
    assert isinstance(result, Scoped)
    assert (result.scope.version, result.scope.visibility, result.scope.where_to_search, result.scope.proposal_id,
            result.scope.confirmed_by) == (1, "public", ["exchanges", "market_data_vendors"], proposed.proposal_id,
                                           "dev-requester")
    assert RequirementScopeRepository(db_session).current(req.id) == result.scope
    assert ProposalRepository(db_session).get(proposed.proposal_id).status == "approved"
    assert status(db_session, req) == "scope_confirmed"


def test_scope_not_confirmed_when_the_requester_rejects(db_session, requirement):
    req = requirement()
    proposed = propose_scope(db_session, requirement_id=req.id, agent=scope_agent(), catalogue=CATALOGUE)
    result = decide_scope(db_session, requirement_id=req.id, user=REQUESTER, decision="rejected",
                          note="It is internal data")
    assert isinstance(result, ScopeNotConfirmed) and result.note == "It is internal data"
    assert ProposalRepository(db_session).get(proposed.proposal_id).status == "rejected"
    assert ProposalRepository(db_session).approvals(proposed.proposal_id)[0].note == "It is internal data"
    assert RequirementScopeRepository(db_session).current(req.id) is None
    assert status(db_session, req) == "scope_not_confirmed"


@pytest.mark.parametrize("start", ["scope_not_confirmed", "no_source_found"])
def test_asking_again_with_a_note_makes_a_new_proposal(db_session, requirement, start):
    req, agent = requirement(start), scope_agent()
    proposed = propose_scope(db_session, requirement_id=req.id, agent=agent, catalogue=CATALOGUE,
                             note="internal desk data", again=True)
    assert agent.inputs[0].note == "internal desk data"
    assert ProposalRepository(db_session).get(proposed.proposal_id).status == "pending"
    assert status(db_session, req) == "awaiting_scope_confirmation"


@pytest.mark.parametrize("start, again", [("defined", True), ("scope_not_confirmed", False),
                                          ("awaiting_scope_confirmation", False), ("onboarding", True)])
def test_propose_from_a_wrong_status_calls_no_agent_and_writes_nothing(db_session, requirement, start, again):
    req, agent = requirement(start), scope_agent()
    before = (count(db_session, ProposalLogEntry), count(db_session, ProposalRow))
    with pytest.raises(RequirementNotInStatus):
        propose_scope(db_session, requirement_id=req.id, agent=agent, catalogue=CATALOGUE, again=again)
    assert agent.inputs == []
    assert (count(db_session, ProposalLogEntry), count(db_session, ProposalRow)) == before
    assert status(db_session, req) == start


def test_a_failed_agent_call_leaves_the_status(db_session, requirement):
    req = requirement()
    with pytest.raises(RuntimeError):
        propose_scope(db_session, requirement_id=req.id, agent=scope_agent(error=RuntimeError("model down")),
                      catalogue=CATALOGUE)
    assert status(db_session, req) == "defined"


def test_a_status_change_during_the_model_call_writes_nothing(db_session, requirement):
    """Review fix 2: after the call the requirement is locked and its status checked again."""
    from sqlalchemy import text

    req = requirement()
    agent = scope_agent()
    agent.during_call = lambda session: session.execute(
        text("UPDATE requirements SET status = 'rejected' WHERE id = :id"), {"id": req.id})
    before = (count(db_session, ProposalLogEntry), count(db_session, ProposalRow))
    with pytest.raises(RequirementNotInStatus):
        propose_scope(db_session, requirement_id=req.id, agent=agent, catalogue=CATALOGUE)
    assert (count(db_session, ProposalLogEntry), count(db_session, ProposalRow)) == before


def test_only_the_requirements_own_requester_decides(db_session, requirement):
    req = requirement()
    propose_scope(db_session, requirement_id=req.id, agent=scope_agent(), catalogue=CATALOGUE)
    other = User(id="dev-other-requester", display_name="Other", email="other@example.test", roles=["requester"])
    before = count(db_session, ApprovalRow)
    with pytest.raises(NotTheRequester):
        decide_scope(db_session, requirement_id=req.id, user=other, decision="approved")
    assert count(db_session, ApprovalRow) == before
    assert status(db_session, req) == "awaiting_scope_confirmation"


def test_a_scope_is_decided_once(db_session, requirement):
    req = requirement()
    propose_scope(db_session, requirement_id=req.id, agent=scope_agent(), catalogue=CATALOGUE)
    decide_scope(db_session, requirement_id=req.id, user=REQUESTER, decision="approved")
    with pytest.raises(RequirementNotInStatus):
        decide_scope(db_session, requirement_id=req.id, user=REQUESTER, decision="rejected")


def test_a_rejected_scope_writes_no_version_so_the_next_confirmed_one_is_version_1(db_session, requirement):
    req = requirement()
    propose_scope(db_session, requirement_id=req.id, agent=scope_agent(), catalogue=CATALOGUE)
    decide_scope(db_session, requirement_id=req.id, user=REQUESTER, decision="rejected", note="private")
    private = {**PUBLIC, "visibility": "private", "where_to_search": ["internal_systems"]}
    propose_scope(db_session, requirement_id=req.id, agent=scope_agent(private), catalogue=CATALOGUE, again=True)
    result = decide_scope(db_session, requirement_id=req.id, user=REQUESTER, decision="approved")
    assert (result.scope.version, result.scope.visibility) == (1, "private")  # the rejected one wrote no scope
