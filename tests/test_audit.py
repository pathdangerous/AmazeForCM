"""Phase 7.15 T4: the monthly audit view and its CSV (plan docs/plans/phase-7.15.md, 2.4).

One row per decision (an approvals row: who, role, decision, note) with its item, the feed, who made the item (an agent, a
person, rules; a decision by `system` is system), the code version in force, whether it is a waiver; and one row per rerun
(who started it, why). Filters: feed and person. The CSV is the same rows; a cell that starts like a formula is neutralised.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from core.repositories.proposals import ProposalRepository
from core.steps.audit import CSV_COLUMNS, audit_csv, audit_rows
from core.steps.exceptions import add_manual_fix, decide_exception_fixes, load_exceptions
from core.steps.onboarding import _system
from tests.configured import VOLUME_RULE, loadable_feed, volume_csv
from tests.test_exception_fixes import NEGATIVE_RULE, a_held_load_with_three_kinds_of_group, agent, propose  # noqa: F401
from tests.test_exception_rerun import OWNER, STEWARD, group_of, rerun, volume_template, waived  # noqa: F401
from tests.test_exceptions_view import deliver, world  # noqa: F401 (world is a fixture)

MONTH = f"{datetime.now(UTC):%Y-%m}"


def rows_of(session, **kw):
    return audit_rows(session, month=kw.pop("month", MONTH), **kw)


def test_every_decision_and_every_rerun_of_the_month_is_a_row_in_time_order(world):
    volume_template(world)
    held = deliver(world, volume_csv(10, 2))
    waived(world, held.id, VOLUME_RULE)
    rerun(world, held, "Both approved.")
    rows = rows_of(world.session)
    [count] = world.session.execute(text("SELECT count(*) FROM approvals WHERE created_at >= date_trunc('month', now())")).one()
    decisions = [r for r in rows if r.kind == "decision"]
    assert len(decisions) == count and [r.kind for r in rows].count("rerun") == 1
    assert [r.at for r in rows] == sorted(r.at for r in rows)
    mine = [r for r in decisions if r.item == "exception_fix"]
    assert [(r.who, r.role, r.decision, r.waiver, r.by, r.feed_code) for r in mine] == [
        ("dev-data-steward", "data_steward", "approved", True, "manual", "syn_exceptions"),
        ("dev-data-owner", "data_owner", "approved", True, "manual", "syn_exceptions")]
    [again] = [r for r in rows if r.kind == "rerun"]
    assert (again.who, again.decision, again.note, again.rerun_of, again.feed_code) == (
        "dev-data-steward", "started", "Both approved.", held.id, "syn_exceptions")
    assert again.code_version == 1  # the code version its go-live pinned


def test_the_code_version_in_force_and_who_made_the_item_are_shown(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    propose(world, agent, held.id)
    groups = {g.rule_key: g for g in load_exceptions(world.session, held.id).groups}
    decide_exception_fixes(world.session, load_id=held.id, user=STEWARD, decision="approved",
                           group_keys=[g.group_key for g in groups.values()])
    steward = [r for r in rows_of(world.session, person="dev-data-steward") if r.item in ("exception_fix", "transformation")
               and r.note is None]
    assert {r.by for r in steward} == {"agent"} and {r.agent for r in steward} == {"ExceptionFixAgent"}
    assert any(r.waiver for r in steward) and not all(r.waiver for r in steward)
    # the code version in force needs decisions made after a committed code version: tests/test_api_audit.py


def test_a_decision_by_system_is_system(world):
    held = deliver(world, volume_csv(10, 2))
    key = group_of(world, held.id, VOLUME_RULE).group_key
    fix = add_manual_fix(world.session, load_id=held.id, group_key=key, action="refer", reason="Ask.", user=STEWARD)
    ProposalRepository(world.session).record_decision(fix.id, user=_system(fix), decision="rejected", note="Superseded.")
    [row] = [r for r in rows_of(world.session) if r.proposal_id == fix.id]
    assert (row.who, row.by, row.decision, row.note) == ("system", "system", "rejected", "Superseded.")


def test_the_view_filters_by_feed_and_by_person(world):
    volume_template(world)
    other, other_live = loadable_feed(world.session, "syn_audit_other")
    held = deliver(world, volume_csv(10, 2))
    waived(world, held.id, VOLUME_RULE)
    assert {r.feed_code for r in rows_of(world.session, feed_id=world.feed)} == {"syn_exceptions"}
    on_other = rows_of(world.session, feed_id=other)  # its own mappings, rules and go-live were decided: only its rows
    assert on_other and {r.feed_code for r in on_other} == {"syn_audit_other"} and not any(
        r.item == "exception_fix" for r in on_other)
    assert {r.who for r in rows_of(world.session, person="dev-data-owner")} == {"dev-data-owner"}
    assert rows_of(world.session, person="nobody") == []


def test_another_month_is_empty_and_a_bad_month_is_refused(world):
    held = deliver(world, volume_csv(10, 2))
    waived(world, held.id, VOLUME_RULE)
    assert rows_of(world.session, month="2999-01") == [] and rows_of(world.session, month="2000-02") == []
    for bad in ("2026-13", "2026", "26-11", "2026-1", "november"):
        with pytest.raises(ValueError):
            rows_of(world.session, month=bad)


def test_a_rerun_made_before_who_was_recorded_says_so(world):
    volume_template(world)
    held = deliver(world, volume_csv(10, 2))
    waived(world, held.id, VOLUME_RULE)
    started, _ = rerun(world, held)
    world.session.execute(text("ALTER TABLE loads DISABLE TRIGGER trg_loads_c_rerun"))  # a test stands in for 0034's data
    world.session.execute(text("UPDATE loads SET rerun_by = NULL WHERE id = :i"), {"i": started.load_id})
    [row] = [r for r in rows_of(world.session) if r.kind == "rerun"]
    assert row.who is None
    assert dict(zip(CSV_COLUMNS, next(csv.reader(io.StringIO(audit_csv([row]).splitlines()[1])))))["who"] == "not recorded"


def test_the_csv_has_one_header_row_the_same_rows_and_neutralises_formulas(world):
    held = deliver(world, volume_csv(10, 2))
    key = group_of(world, held.id, VOLUME_RULE).group_key
    add_manual_fix(world.session, load_id=held.id, group_key=key, action="refer", reason='=HYPERLINK("http://x","y")', user=STEWARD)
    decide_exception_fixes(world.session, load_id=held.id, group_keys=[key], user=STEWARD, decision="approved")
    rows = rows_of(world.session)
    parsed = list(csv.reader(io.StringIO(audit_csv(rows))))
    assert parsed[0] == list(CSV_COLUMNS) and len(parsed) == len(rows) + 1
    cells = [dict(zip(CSV_COLUMNS, line)) for line in parsed[1:]]
    summary = next(c["summary"] for c in cells if "HYPERLINK" in c["summary"])
    assert summary.startswith("'=HYPERLINK")  # a spreadsheet never runs it
    assert all(not c[k].startswith(("=", "+", "@")) for c in cells for k in c)
    assert {c["waiver"] for c in cells} == {"no"} and cells[0]["time"].startswith(MONTH)
