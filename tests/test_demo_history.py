"""Phase 7.16 T0b: `python -m core.seed --demo-history` (plan docs/plans/phase-7.16.md, 2.2).

21 past days of a feed's deliveries through the real daily pipeline (DailyFetch: fetch, step 12, the engine, steps 15 and
20) with an injected clock and an injected connector. The same rule fails on every Monday, so one weekday pattern and
non-zero reports appear. Nothing is inserted into a guarded table by the seed itself.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from apps.api import demo_history
from apps.api.demo_history import HistoryRefused, HistorySeeded, HistorySpec, seed_demo_history
from apps.api.jobs import DailyFetch
from core.domain.report import Period
from core.repositories.loads import LoadRepository
from core.steps.patterns import find_patterns
from core.steps.reports import build_numbers
from ports.config import Settings
from ports.connectors.local_file import LocalFileConnector
from ports.storage.local import LocalBlobStore
from tests.configured import VOLUME_RULE, loadable_feed, volume_csv

SPEC = HistorySpec(feed_code="syn_history", column="traded_qty", rule_key=VOLUME_RULE, bad_value="200000", bad_rows=4,
                   date_column="trade_date", date_format="%Y-%m-%d")
TODAY = date.today()
DAYS = [TODAY - timedelta(days=n) for n in range(21, 0, -1)]


@pytest.fixture
def world(db_session, glossary, tmp_path):
    """A registered feed with a go-live, and a file fetched once (no load of today), as after the demo's first load."""
    return build_world(db_session, tmp_path)


def build_world(db_session, tmp_path, schedule=None):
    """schedule (phase 7.21): the feed's version 1, as the upload screen's question gives it; None is the helper's daily one."""
    from core.domain.profiling import FileColumn
    from core.repositories.feeds import FeedRepository
    from core.repositories.schedules import FeedScheduleRepository
    from tests.configured import approved_mapping, approved_rule, go_live
    from tests.onboarded import registered_feed

    feed = FeedRepository(db_session).create_candidate(code="syn_history", name="History", source_code="syn_history",
                                                       connector="local_file", source_ref="files/syn_history.csv")
    registered_feed(db_session, feed.id, scheduled=schedule is None, columns=[  # the template step 12 compares each delivery with
        FileColumn(name=n, type="VARCHAR", null_count=0, null_rate=0.0, distinct_count=1)
        for n in ("instr_id", "trade_date", "traded_qty")])
    if schedule is not None:
        FeedScheduleRepository(db_session).add_version(feed_id=feed.id, schedule=schedule, decided_by="dev-backend-team")
    for field, key in (("instr_id", "instrument_id"), ("trade_date", "price_date"), ("traded_qty", "volume")):
        approved_mapping(db_session, feed.id, field, key)
    approved_rule(db_session, feed.id, VOLUME_RULE, sql="SELECT _row FROM curated WHERE volume > 100000",
                  description="Volume is at most 100000.")
    go_live(db_session, feed.id)
    feed_id = feed.id
    files = tmp_path / "sources" / "files"
    files.mkdir(parents=True)
    (files / "syn_history.csv").write_text(volume_csv(rows=10), encoding="utf-8")
    db_session.commit()
    daily = DailyFetch(session_factory=lambda: nullcontext(db_session), blob_store=LocalBlobStore(tmp_path / "blob"),
                       connector_factory=lambda name: LocalFileConnector(tmp_path / "sources"),
                       backend_team=lambda: ["dev-backend-team"])
    assert daily._fetch_one(feed_id, today=None, catalogue=None) == "fetched"
    return feed_id, daily


def scheduled(db_session, feed_id):
    return [load for load in LoadRepository(db_session).list_for_feed(feed_id) if load.purpose == "scheduled"]


def test_the_history_is_21_days_through_the_real_pipeline_with_mondays_held(db_session, world):
    feed_id, daily = world
    result = seed_demo_history(daily, today=TODAY, spec=SPEC)
    assert isinstance(result, HistorySeeded) and (result.loaded, result.skipped, result.held) == (21, 0, 3)
    loads = sorted(scheduled(db_session, feed_id), key=lambda load: load.received_at)
    assert [load.received_at.astimezone(UTC).date() for load in loads] == DAYS  # the injected clock
    assert {load.arrival_outcome for load in loads} == {"on_time_complete"}  # step 12 ran on every one
    assert [load.status for load in loads] == ["held" if day.weekday() == 0 else "released" for day in DAYS]
    assert all([s.outcome for s in LoadRepository(db_session).stages(load.id)] == ["stage_completed"] * 4 for load in loads)


def test_the_demo_history_has_one_missing_day_and_two_late_arrivals_on_the_deliveries_dashboard(db_session, glossary, tmp_path):
    """Phase 7.21, T5: the demo answers "Recurring, business days, 07:00, 60 min grace"; the files come at 07:20, one weekday
    nothing comes (the source does not answer), two others the file comes at 10:30. Every row is made by the pipeline."""
    from datetime import time

    from core.domain.schedule import ScheduleInput
    from core.steps.deliveries import deliveries_for_feed

    feed_id, daily = build_world(db_session, tmp_path, ScheduleInput(
        kind="recurring", frequency="business_days", expected_time=time(7, 0), grace_minutes=60))
    spec = demo_history.demo_spec(TODAY).model_copy(update={
        "feed_code": "syn_history", "column": "traded_qty", "rule_key": VOLUME_RULE, "bad_value": "200000", "bad_rows": 4,
        "date_column": "trade_date", "date_format": "%Y-%m-%d"})
    (missing_ago,), late_ago = spec.missing_days_ago, spec.late_days_ago
    assert len(late_ago) == 2 and 1 not in (missing_ago, *late_ago)  # yesterday keeps its load
    assert (TODAY - timedelta(days=missing_ago)).weekday() in (1, 2, 3)  # not a Monday (the weekday pattern) or a Friday
    result = seed_demo_history(daily, today=TODAY, spec=spec)
    assert isinstance(result, HistorySeeded) and (result.loaded, result.missing, result.late, result.problems) == (20, 1, 2, [])

    midnight = datetime.combine(TODAY, datetime.min.time(), tzinfo=UTC)  # today's own 07:00 is not yet a row
    shown = deliveries_for_feed(db_session, feed_id, window="30", now=midnight)
    by_day = {r.due_at.date(): r for r in shown.rows}
    assert by_day[TODAY - timedelta(days=missing_ago)].status == "missing"
    assert [by_day[TODAY - timedelta(days=n)].late_minutes for n in late_ago] == [150] * 2  # 10:30 against 08:00
    weekdays = [d for d in DAYS if d.weekday() < 5]
    assert sorted(by_day) == weekdays and shown.figures.late == 2 and shown.figures.missing == 1
    assert shown.figures.expected == len(weekdays) and shown.figures.on_time == len(weekdays) - 3
    weekends = [d for d in DAYS if d.weekday() >= 5]
    # a file on a day nothing is due is "unexpected", never late or missing (a Saturday just before the window's first
    # delivery belongs to a Friday that is not in the window, so it may not be listed)
    assert len(shown.unexpected) in (len(weekends) - 1, len(weekends))
    assert shown.figures.streak >= 1  # yesterday is on time


def test_the_same_rule_failing_on_every_monday_makes_one_weekday_pattern(db_session, world):
    feed_id, daily = world
    seed_demo_history(daily, today=TODAY, spec=SPEC)
    patterns = find_patterns(db_session, feed_ids=[feed_id], as_of=datetime.combine(TODAY + timedelta(days=1), datetime.min.time(),
                                                                                   tzinfo=UTC), window_days=90, min_loads=3)
    assert [(p.rule_key, p.dimension, p.value, p.failing_loads, p.loads_in_window) for p in patterns] == [
        (VOLUME_RULE, "weekday", "Monday", 3, 3)]


def test_the_reports_of_those_days_are_not_empty(db_session, world):
    feed_id, daily = world
    seed_demo_history(daily, today=TODAY, spec=SPEC)
    yesterday = build_numbers(db_session, subject_id="dev-data-owner", role="data_owner", period=Period.daily(TODAY - timedelta(days=1)),
                              settings=Settings(_env_file=None))
    assert yesterday.totals.loads == 1 and yesterday.totals.rows == 10
    mondays = [day for day in DAYS if day.weekday() == 0]
    first, last = (build_numbers(db_session, subject_id="dev-data-owner", role="data_owner", period=Period.daily(day),
                                 settings=Settings(_env_file=None)) for day in (mondays[0], mondays[-1]))
    assert (first.totals.held, first.totals.flagged_rows, first.feeds[0].patterns) == (1, 4, 0)  # one failure is no pattern yet
    assert (last.totals.held, last.totals.flagged_rows, last.feeds[0].patterns) == (1, 4, 1)  # the third Monday is


def test_running_it_again_changes_nothing(db_session, world):
    feed_id, daily = world
    seed_demo_history(daily, today=TODAY, spec=SPEC)
    again = seed_demo_history(daily, today=TODAY, spec=SPEC)
    assert isinstance(again, HistorySeeded) and (again.loaded, again.skipped) == (0, 21)
    assert len(scheduled(db_session, feed_id)) == 21


@pytest.mark.parametrize("spec, reason", [
    (SPEC.model_copy(update={"feed_code": "syn_no_such_feed"}), "feed_missing"),
    (SPEC.model_copy(update={"column": "no_such_column"}), "column_missing"),
    (SPEC.model_copy(update={"bad_rows": 10}), "too_few_rows"),
])
def test_it_refuses_in_words_and_loads_nothing(db_session, world, spec, reason):
    feed_id, daily = world
    result = seed_demo_history(daily, today=TODAY, spec=spec)
    assert isinstance(result, HistoryRefused) and result.reason == reason and result.detail
    assert scheduled(db_session, feed_id) == []


def test_a_feed_without_a_go_live_or_a_file_is_refused(db_session, glossary, tmp_path):
    from core.repositories.feeds import FeedRepository
    from tests.onboarded import registered_feed

    feed = FeedRepository(db_session).create_candidate(code="syn_history", name="History", source_code="syn_history",
                                                       connector="local_file", source_ref="files/syn_history.csv")
    registered_feed(db_session, feed.id)
    db_session.commit()
    daily = DailyFetch(session_factory=lambda: nullcontext(db_session), blob_store=LocalBlobStore(tmp_path / "blob"),
                       connector_factory=lambda name: LocalFileConnector(tmp_path), backend_team=lambda: [])
    assert seed_demo_history(daily, today=TODAY, spec=SPEC).reason == "not_live"


def test_the_seed_inserts_nothing_into_a_table_itself():
    source = Path(demo_history.__file__).read_text(encoding="utf-8")
    assert not any(word in source for word in ("INSERT", "insert(", "session.add", "execute(text", "UPDATE ", "DELETE "))
    assert "tables" not in source.replace("guarded tables", "")  # it imports no table class


def test_the_demo_defaults_are_the_security_masters_and_its_date_column_is_written_in_the_files_format():
    from apps.api.demo_history import _day_file

    spec = HistorySpec()
    assert (spec.feed_code, spec.column, spec.rule_key, spec.date_column, spec.date_format) == (
        "syn_security_master", "BEST_SALES", "best_sales_not_negative", "RECORD_DATE", "%d-%m-%Y")
    assert "unique_column" not in HistorySpec.model_fields  # a rule failing every day is reported once, not hidden (7.16)
    header = ["TICKER_NAME", "RECORD_DATE", "BEST_SALES"]
    rows = [["T0000" if n <= 2 else f"T{n:04d}", "31-12-2019", str(-n if n % 2 else n)] for n in range(1, 13)]  # rows 1, 2: one ticker
    monday = date(2026, 11, 2)
    lines = _day_file(header, rows, spec, monday, 5).decode().splitlines()[1:]
    assert {line.split(",")[1] for line in lines} == {"02-11-2026"}  # every row is of that day, written as the file writes dates
    assert [line.split(",")[2] for line in lines[:8]] == ["-1"] * 8  # a Monday: the first eight are bad
    assert len({line.split(",")[0] for line in lines}) == 11  # the file's own duplicate is left as it is
    ordinary = _day_file(header, rows, spec, date(2026, 11, 3), 6).decode().splitlines()[1:]
    assert all(float(line.split(",")[2]) >= 0 for line in ordinary)  # any other day: nothing negative
    assert ordinary != lines


def test_the_command_prints_the_outcome_and_stops_with_1_when_refused(monkeypatch, capsys):
    from core import seed

    monkeypatch.setattr(demo_history, "run", lambda: HistorySeeded(loaded=21, skipped=0, held=3, rule_found=True, problems=[]))
    seed.main(["--demo-history"])
    assert "demo history: 21 days loaded, 0 already there, 3 held" in capsys.readouterr().out
    monkeypatch.setattr(demo_history, "run", lambda: HistoryRefused(reason="feed_missing", detail="There is no feed."))
    with pytest.raises(SystemExit) as stopped:
        seed.main(["--demo-history"])
    assert stopped.value.code == 1 and "There is no feed." in capsys.readouterr().err


def test_the_clock_gives_only_the_loads_received_at(db_session, world):
    """The injected clock is the load's received_at; every other timestamp is the database's own."""
    feed_id, daily = world
    seed_demo_history(daily, today=TODAY, spec=SPEC)
    now = datetime.now(UTC)
    created = db_session.scalars(text("SELECT created_at FROM loads WHERE feed_id = :f AND purpose = 'scheduled'"), {"f": feed_id}).all()
    assert all(c > now - timedelta(minutes=30) for c in created)
