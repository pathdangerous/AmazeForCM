"""Phase 7.22, T2: mapping runs before the glossary (docs/plans/phase-7.22.md, decisions D1 to D3).

- The order is Profile, Mapping, Glossary requests, Transformations, Rules, Go-live, First load, Product ready.
- Registering the feed in the onboarding queues no glossary run; the mapping step maps to the existing glossary only.
- A column no element fits is a "cannot map" column (not a proposal, not a send-back to the glossary): a person chooses
  Ignore for it, and Approve & next waits until every such column is decided.
- Ignore is the existing excluded mapping: a manual proposal the steward approves; the column is not loaded.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from core.domain.onboarding import ONBOARDING_STEPS
from core.repositories.configuration import MappingRepository
from core.repositories.errors import DecisionNeeded, StepNotEnterable
from core.repositories.proposals import ProposalRepository
from core.steps.onboarding import approve_and_next, decide_cannot_map
from core.steps.s06_map import decide_mapping, excluded_columns
from tests.test_onboarding import OPERATOR, approve, current, events, make_world, view, work


def at_mapping(db_session, tmp_path):
    w = make_world(db_session, tmp_path)
    work(w)
    approve(w)  # the profile registers the feed
    work(w)  # the mapping agent answers
    return w


def settle_mapping_items(w) -> None:
    """The steward decides the items that need a person (a tie): the first candidate."""
    for item in [i for i in view(w).items if i.needs_decision]:
        decide_mapping(w.session, proposal_id=item.proposal_id, user=OPERATOR, decision="approved",
                       choice=item.payload.get("recommended") or item.payload["candidates"][0], note=None)
    w.session.commit()


def runs(s, feed_id) -> int:
    return s.scalar(text("SELECT count(*) FROM glossary_runs WHERE feed_id = :f"), {"f": feed_id})


def test_the_order_is_mapping_before_glossary_requests():
    assert ONBOARDING_STEPS == ("upload", "profile", "mapping", "glossary", "transformations", "rules", "go_live",
                                "first_load", "product_ready")


def test_registering_in_the_onboarding_queues_no_glossary_run_and_the_mapping_step_follows(db_session, glossary, tmp_path):
    w = at_mapping(db_session, tmp_path)
    assert current(w).current_step == "mapping"
    assert runs(db_session, w.feed_id) == 0, "no glossary run at registration, none sent back by the mapping step"


def test_a_column_no_element_fits_is_a_cannot_map_item_and_blocks_approve_until_decided(db_session, glossary, tmp_path):
    w = at_mapping(db_session, tmp_path)
    ws = view(w)
    assert ws.cannot_map, "the demo file has columns the seed glossary has no element for"
    assert {c.state for c in ws.cannot_map} == {"undecided"}
    assert {c.reason for c in ws.cannot_map} <= {"no_candidate", "type_does_not_fit", "every_candidate_rejected"}
    assert [e.detail["cannot_map"] for e in events(w, "agent_done", "mapping")][-1] == {
        c.column: c.reason for c in ws.cannot_map}, "the outcome is recorded in the event"
    settle_mapping_items(w)  # only the columns nobody chose for are left in the way
    ws = view(w)
    assert not ws.can_approve and "cannot be mapped" in (ws.blocked_by or "")
    with pytest.raises(DecisionNeeded):
        approve_and_next(db_session, session_id=w.session_id, user=OPERATOR, ctx=w.ctx)
    db_session.rollback()
    assert runs(db_session, w.feed_id) == 0


def test_ignore_is_the_excluded_mapping_and_unblocks_the_step(db_session, glossary, tmp_path):
    w = at_mapping(db_session, tmp_path)
    columns = [c.column for c in view(w).cannot_map]
    decided = decide_cannot_map(db_session, session_id=w.session_id, action="ignore", columns=columns, user=OPERATOR)
    db_session.commit()
    assert [p.payload["source_field"] for p in decided.proposals] == columns
    proposals = ProposalRepository(db_session)
    for p in decided.proposals:
        assert (p.origin, p.author, p.payload["excluded"], p.status) == ("manual", OPERATOR.id, True, "pending")
        assert proposals.get(p.id).proposal_log_id is None
    ws = view(w)
    assert {c.state for c in ws.cannot_map} == {"ignored"}
    assert not any(i.needs_decision for i in ws.items if i.payload.get("excluded"))

    settle_mapping_items(w)  # the other items are the steward's as always
    approve(w)
    assert current(w).current_step == "glossary"
    assert set(columns) <= excluded_columns(db_session, w.feed_id)
    mapped = {m.source_field for m in MappingRepository(db_session).current_for_feed(w.feed_id)}
    assert mapped and not (mapped & set(columns)), "an ignored column is not mapped, so it is not loaded"


def test_a_column_that_is_not_cannot_map_cannot_be_ignored_this_way(db_session, glossary, tmp_path):
    w = at_mapping(db_session, tmp_path)
    mapped = next(i.column for i in view(w).items if i.kind == "mapping" and not i.payload.get("excluded"))
    with pytest.raises(StepNotEnterable):
        decide_cannot_map(db_session, session_id=w.session_id, action="ignore", columns=[mapped], user=OPERATOR)
    db_session.rollback()
