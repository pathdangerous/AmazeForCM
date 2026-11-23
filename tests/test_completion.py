"""Phase 7.12, T2: the completion report before go-live. Read only, no model, and it writes nothing: it counts what the
approved configuration, the pending items and the agent and manual items of one feed say."""

from __future__ import annotations

from sqlalchemy import func, select

from core.domain.manual import ManualMapping
from core.proposal_log import ProposalLogEntry
from core.repositories.proposals import ProposalRepository
from core.steps.completion import completion_report
from core.steps.onboarding import add_item
from core.steps.s06_map import decide_mapping
from tests.test_onboarding import OPERATOR, make_world, run_to, view


def test_the_report_counts_mappings_excluded_columns_rules_actions_and_who_made_each_item(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "mapping")
    first = next(i for i in view(w).items if i.kind == "mapping" and i.status == "pending")
    decide_mapping(db_session, proposal_id=first.proposal_id, user=OPERATOR, decision="rejected", choice=None, note="n")
    add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualMapping(
        kind="mapping", source_field=first.column, excluded=True, reason="A scratch column."))
    run_to(w, "go_live")
    calls = db_session.scalar(select(func.count()).select_from(ProposalLogEntry))

    report = completion_report(db_session, w.feed_id)

    assert db_session.scalar(select(func.count()).select_from(ProposalLogEntry)) == calls  # no model, nothing written
    m = report.mappings
    assert m.excluded_columns == [first.column] and m.excluded == 1
    assert m.done == len(m.mapped_columns) > 0 and m.pending == 0
    assert first.column not in {c.column for c in m.mapped_columns}
    assert m.done + m.excluded + m.undecided == report.columns_in_file
    assert all(t.column and t.steps for t in report.transformations.current)
    for rule in report.rules.current:
        assert rule.rule_key and rule.backtest_status in ("ran", "not_back_tested", "none")
        assert rule.flagged_rows is None or 0 <= rule.flagged_rows <= (rule.total_rows or 0)
    approved = ProposalRepository(db_session).list_for_feed(w.feed_id, "profile_action", "approved")
    assert report.actions.by_agent == sum(a.payload["applied_by"] == "agent" for a in approved)
    assert report.actions.by_person == sum(a.payload["applied_by"] == "manual" for a in approved)
    assert report.items.manual >= 1 and report.items.agent > report.items.manual
    assert report.items.manual_by_kind.get("mapping") == 1
    assert report.ready_for_go_live in (True, False) and isinstance(report.words, list) and report.words


def test_a_feed_with_nothing_configured_has_an_honest_empty_report(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    report = completion_report(db_session, w.feed_id)
    assert (report.mappings.done, report.mappings.excluded, report.rules.current, report.transformations.current) == (0, 0, [], [])
    assert report.ready_for_go_live is False
