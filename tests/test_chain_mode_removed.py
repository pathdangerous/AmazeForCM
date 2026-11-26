"""Phase 7.22, T1: "Run all agents" (phase 7.13 chain mode) is gone, and per-step "Approve all confident" stays without it.

- No route runs the chain or shows a chain review; the session has no run_mode in the API.
- Approve all confident is one approval per confident item of the current step, by the person who clicked (rule 1), through
  `POST /api/amaze/onboardings/{id}/steps/{step}/approve-confident`; items that need a decision are left.
"""

from __future__ import annotations

from apps.api.main import app
from core.domain.onboarding import ConfidentApproved, OnboardingSession
from core.repositories.proposals import ProposalRepository
from core.steps.onboarding import approve_confident
from tests.test_onboarding import OPERATOR, approve, current, events, make_world, settle, view, work

PREFIX = "/api/amaze/onboardings/{session_id}"


def paths() -> set[tuple[str, str]]:
    return {(m.upper(), path) for path, ops in app.openapi()["paths"].items() for m in ops}


def test_the_chain_routes_are_gone_and_approve_confident_has_its_own_step_route():
    found = paths()
    assert ("POST", PREFIX + "/run-all") not in found
    assert ("GET", PREFIX + "/chain") not in found
    assert ("POST", PREFIX + "/chain/approve-confident") not in found
    assert ("POST", PREFIX + "/steps/{step}/approve-confident") in found


def test_a_session_has_no_run_mode():
    assert "run_mode" not in OnboardingSession.model_fields


def test_approve_all_confident_approves_only_the_confident_items_of_the_current_step(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    work(w)
    approve(w)  # the profile registers the feed
    work(w)
    for _ in range(4):  # the first item step that has both a confident item and one a person must decide
        step, items = current(w).current_step, view(w).items
        confident = sorted(i.proposal_id for i in items if i.pre_accepted and i.status == "pending")
        others = {i.proposal_id for i in items} - set(confident)
        if confident and others:
            break
        settle(w)
        approve(w)
        work(w)
    assert confident and others, "the fixture must have both kinds of item"

    done = approve_confident(db_session, session_id=w.session_id, step=step, user=OPERATOR, ctx=w.ctx)
    db_session.commit()

    assert isinstance(done, ConfidentApproved) and sorted(done.approved) == confident
    assert done.workspace.current_step == step
    proposals = ProposalRepository(db_session)
    for proposal_id in confident:
        decisions = proposals.approvals(proposal_id)  # a rule has two approver steps; each is a row
        assert decisions and {(d.decision, d.decided_by) for d in decisions} == {("approved", OPERATOR.id)}
    assert all(proposals.get(i).status == "pending" for i in others), "an item that needs a decision is left"
    assert {e.proposal_id for e in events(w, "approved")} >= set(confident)
    assert current(w).current_step == step, "approving the confident items does not move the step on"
