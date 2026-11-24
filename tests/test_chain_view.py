"""Phase 7.13, T2: the chain review (plan, decision 7; review answers Q2, Q3).

Every draft, grouped by step, with its confidence and what it was built on. Blocked items (no single recommended
value) sort first, then low-confidence ones. "Approve all confident" is the person's own decision on each item: one
approval row per item, and only items whose bases are approved.
"""

from __future__ import annotations

from core.repositories.proposals import ProposalRepository
from core.steps.onboarding import CHAIN_STEPS, approve_confident, chain_review
from tests.test_chain_review import drafted
from tests.test_onboarding import OPERATOR, events


def review(w):
    return chain_review(w.session, session_id=w.session_id, settings=w.settings)


def rank(i) -> int:
    return 0 if i.blocked else 1 if i.low_confidence else 2


def test_the_review_shows_every_draft_by_step_with_what_it_was_built_on(db_session, glossary, tmp_path):
    w = drafted(db_session, tmp_path)
    chain = review(w)
    assert [s.step for s in chain.steps] == list(CHAIN_STEPS) and chain.run_mode == "chain"
    proposals = ProposalRepository(db_session)
    for step in chain.steps:
        assert step.items, step.step
        assert [rank(i) for i in step.items] == sorted(rank(i) for i in step.items), "blocked, then low confidence"
        for i in step.items:
            assert [b.proposal_id for b in i.based_on] == proposals.bases(i.item.proposal_id)
            if i.item.confidence is None and i.item.kind in ("mapping", "glossary_element"):
                assert i.blocked or i.item.payload.get("recommended") is not None
    mappings = next(s for s in chain.steps if s.step == "mapping").items
    linked = [i for i in mappings if i.based_on]
    assert linked and all(b.step == "glossary" and b.status == "pending" for i in linked for b in i.based_on)
    assert not any(i.confident for i in linked), "built on a draft not approved yet"


def test_approve_all_confident_is_one_approval_per_item_by_the_person(db_session, glossary, tmp_path):
    w = drafted(db_session, tmp_path)
    confident = [i.item.proposal_id for s in review(w).steps if s.step == "glossary" for i in s.items if i.confident]
    assert confident

    done = approve_confident(db_session, session_id=w.session_id, step="glossary", user=OPERATOR, ctx=w.ctx)
    db_session.commit()

    assert sorted(done.approved) == sorted(confident)
    proposals = ProposalRepository(db_session)
    for proposal_id in confident:
        [decision] = proposals.approvals(proposal_id)
        assert (decision.decision, decision.decided_by) == ("approved", OPERATOR.id)
    assert {e.proposal_id for e in events(w, "approved")} >= set(confident)
    # the mapping drafts built on those glossary drafts are now confident too, if the agent was sure of them
    mapping = next(s for s in done.chain.steps if s.step == "mapping")
    assert any(i.confident for i in mapping.items if i.based_on)
