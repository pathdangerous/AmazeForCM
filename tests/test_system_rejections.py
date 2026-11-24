"""Phase 7.13, T4: a rejection by `system` (Back's rerun, a stale rerun) is not a person's judgement.

rejected_elements() and rejected_keys() are the "not proposed again" memory of steps 06 and 09. Only a person's
rejection may feed it; a proposal `system` rejected so that its step can run again must be proposable again.
"""

from __future__ import annotations

from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.proposals import ProposalRepository
from core.steps.onboarding import back
from tests.test_onboarding import OPERATOR, approve, current, make_world, run_to, work


def _pending_mappings(w) -> dict[str, str | None]:
    return {p.payload["source_field"]: p.payload.get("recommended")
            for p in ProposalRepository(w.session).list_for_feed(w.feed_id, "mapping", "pending")}


def test_after_back_the_mapping_agent_proposes_the_same_elements_again(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    work(w)
    approve(w)  # the profile: registers the feed
    run_to(w, "glossary")
    approve(w)
    work(w)  # the mapping agent answers; its proposals are pending
    assert current(w).current_step == "mapping"
    before = {c: e for c, e in _pending_mappings(w).items() if e}
    assert before

    back(db_session, session_id=w.session_id, to_step="mapping", user=OPERATOR)  # system rejects the pending ones
    db_session.commit()
    work(w)

    after = _pending_mappings(w)
    assert {c: after.get(c) for c in before} == before
    column = next(iter(before))
    assert MappingRepository(db_session).rejected_elements(w.feed_id, column) == set()


def test_a_persons_rejection_still_counts_and_a_system_one_does_not(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    work(w)
    approve(w)
    run_to(w, "glossary")
    approve(w)
    work(w)
    proposals = ProposalRepository(db_session)
    first, second = proposals.list_for_feed(w.feed_id, "mapping", "pending")[:2]
    proposals.record_decision(first.id, user=OPERATOR, decision="rejected", note="wrong element")
    system = OPERATOR.model_copy(update={"id": "system"})
    proposals.record_decision(second.id, user=system, decision="rejected", note="rerun after Back")
    db_session.flush()
    mappings = MappingRepository(db_session)
    assert mappings.rejected_elements(w.feed_id, first.payload["source_field"]) == set(first.payload["candidates"])
    assert mappings.rejected_elements(w.feed_id, second.payload["source_field"]) == set()


def test_a_rule_key_system_rejected_may_be_proposed_again(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "rules")
    work(w)
    proposals, rules = ProposalRepository(db_session), RuleRepository(db_session)
    earlier = rules.rejected_keys(w.feed_id)  # the operator's own rejections while settling the step
    first, second = proposals.list_for_feed(w.feed_id, "rule", "pending")[:2]
    proposals.record_decision(first.id, user=OPERATOR, decision="rejected", note="wrong for this data")
    system = OPERATOR.model_copy(update={"id": "system"})
    proposals.record_decision(second.id, user=system, decision="rejected", note="rerun after Back")
    db_session.flush()
    assert rules.rejected_keys(w.feed_id) == earlier | {first.payload["rule_key"]}
