"""Phase 7.13, T3: the chain is approved top to bottom, and an edit or a rejection re-runs only what it made stale
(plan, decisions 3, 5 and 7).

- Approving step by step in chain mode runs no agent again for a step that is already drafted; the go-live step
  prepares as today (its test load and code version need approved configuration).
- A mapping row exists only after its approval, a rule row only after its approval.
- A draft cannot be approved before the drafts it was built on: UpstreamNotApproved, nothing written.
- An edit or a rejection of a draft rejects (as `system`) the drafts built on it, and only their steps run again.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from core.repositories.errors import UpstreamNotApproved
from core.repositories.proposals import ProposalRepository
from core.steps.onboarding import CHAIN_STEPS, STALE_NOTE, edit_item, reject_item, run_all
from core.steps.s06_map import decide_mapping
from tests.test_onboarding import OPERATOR, approve, current, events, make_world, settle, view, work


def rows(s, table, feed_id) -> int:
    return s.scalar(text(f"SELECT count(*) FROM {table} WHERE feed_id = :f"), {"f": feed_id})


def drafted(db_session, tmp_path, **kw):
    w = make_world(db_session, tmp_path, **kw)
    run_all(db_session, session_id=w.session_id, user=OPERATOR)
    db_session.commit()
    work(w)
    approve(w)  # the profile: the one click
    work(w)  # every agent drafts
    assert all(current(w).step_states[s] in ("ready", "needs_decision") for s in CHAIN_STEPS)
    return w


def mapping_on_a_glossary_draft(w):
    proposals = ProposalRepository(w.session)
    for m in proposals.list_for_feed(w.feed_id, "mapping", "pending"):
        bases = proposals.bases(m.id)
        if bases and m.payload["recommended"]:
            return m, proposals.get(bases[0])
    raise AssertionError("no mapping draft is built on a glossary draft")


def test_approving_top_to_bottom_reaches_the_go_live_review_and_runs_no_drafted_agent_again(db_session, glossary,
                                                                                         tmp_path):
    w = drafted(db_session, tmp_path)
    s = db_session
    calls = current(w).model_calls
    seen = {}
    while current(w).current_step != "go_live":
        step = current(w).current_step
        seen[step] = (rows(s, "mappings", w.feed_id), rows(s, "rules", w.feed_id))
        settle(w)  # a tie, a conflict, a rule that flags rows of this file: the person decides
        approve(w)
        work(w)
    assert current(w).model_calls == calls + 1, "only the go-live summary was asked for"
    assert seen["glossary"] == (0, 0) and seen["mapping"] == (0, 0), "no mapping row before its approval"
    assert seen["transformations"][0] > 0 and seen["rules"][1] == 0, "no rule row before its approval"
    assert rows(s, "rules", w.feed_id) > 0
    [summary] = [i for i in view(w).items if i.kind == "review_summary"]
    assert summary.status == "pending"  # the owner's go-live review, prepared as today (test load, summary)


def test_a_draft_is_not_approved_before_the_draft_it_was_built_on(db_session, glossary, tmp_path):
    w = drafted(db_session, tmp_path)
    mapping, _ = mapping_on_a_glossary_draft(w)
    with pytest.raises(UpstreamNotApproved):
        decide_mapping(db_session, proposal_id=mapping.id, user=OPERATOR, decision="approved", choice=None, note=None)
    db_session.rollback()
    assert rows(db_session, "mappings", w.feed_id) == 0
    assert ProposalRepository(db_session).get(mapping.id).status == "pending"


def test_an_edit_upstream_makes_its_drafts_stale_and_reruns_only_their_steps(db_session, glossary, tmp_path):
    w = drafted(db_session, tmp_path)
    proposals = ProposalRepository(db_session)
    mapping, base = mapping_on_a_glossary_draft(w)
    others = {m.id for m in proposals.list_for_feed(w.feed_id, "mapping", "pending")} - {mapping.id}
    calls = current(w).model_calls

    edited = edit_item(db_session, session_id=w.session_id, proposal_id=base.id,
                       payload=dict(base.payload, reason="Checked by the glossary owner."), user=OPERATOR)
    db_session.commit()

    assert proposals.get(mapping.id).status == "rejected"
    [decision] = proposals.approvals(mapping.id)
    assert (decision.decided_by, decision.note) == ("system", STALE_NOTE)
    assert mapping.id in {e.proposal_id for e in events(w, "draft_stale")}
    assert {m.id for m in proposals.list_for_feed(w.feed_id, "mapping", "pending")} == others
    assert current(w).step_states["mapping"] in ("stale", "agent_working")

    ran = work(w)
    again = [p for p in proposals.list_for_feed(w.feed_id, "mapping", "pending")
             if p.payload["source_field"] == mapping.payload["source_field"]]
    assert len(again) == 1 and proposals.bases(again[0].id) == [edited.id], "built on the edited draft"
    assert again[0].payload["recommended"] == mapping.payload["recommended"], "a system rejection is no judgement"
    assert {r.step for r in ran} <= set(CHAIN_STEPS) - {"glossary"}, "the glossary agent is not asked again"
    assert current(w).model_calls == calls + len(ran)
    mapping_items = {i.proposal_id for i in _items(w, "mapping")}  # the step's items: the untouched and the new
    assert others <= mapping_items and again[0].id in mapping_items and mapping.id not in mapping_items


def test_an_edited_draft_is_still_built_on_what_it_was_built_on(db_session, glossary, tmp_path):
    w = drafted(db_session, tmp_path)
    proposals = ProposalRepository(db_session)
    mapping, base = mapping_on_a_glossary_draft(w)
    edited = edit_item(db_session, session_id=w.session_id, proposal_id=mapping.id,
                       payload=dict(mapping.payload, reason="Checked by the data steward."), user=OPERATOR)
    db_session.commit()
    assert proposals.bases(edited.id) == proposals.bases(mapping.id)
    with pytest.raises(UpstreamNotApproved):  # so it still waits for its glossary draft
        decide_mapping(db_session, proposal_id=edited.id, user=OPERATOR, decision="approved", choice=None, note=None)
    db_session.rollback()


def test_a_rejection_upstream_makes_its_drafts_stale_too(db_session, glossary, tmp_path):
    w = drafted(db_session, tmp_path)
    proposals = ProposalRepository(db_session)
    mapping, base = mapping_on_a_glossary_draft(w)
    # more pending rules than MAX_KNOWN_KEYS (20): the rules rerun must still not propose a pending key again
    assert len(proposals.list_for_feed(w.feed_id, "rule", "pending")) > 20
    reject_item(db_session, session_id=w.session_id, proposal_id=base.id, user=OPERATOR, note="Not an element.")
    db_session.commit()
    assert proposals.get(base.id).status == "rejected"
    assert proposals.get(mapping.id).status == "rejected"
    assert proposals.approvals(mapping.id)[0].decided_by == "system"
    work(w)
    assert all(current(w).step_states[s] in ("ready", "needs_decision") for s in CHAIN_STEPS)


def _items(w, step):
    from core.steps.onboarding import workspace

    return workspace(w.session, session_id=w.session_id, settings=w.settings, step=step).items
