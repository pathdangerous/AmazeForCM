"""Phase 7.21, T3 (plan docs/plans/phase-7.21.md, 2.5 to 2.11): expected deliveries and the load that arrived for each.

Pure code: no database, no clock (`now` is given). Every status, the time zone, the schedule in force when a delivery was
due, deliveries that nobody expected, and the figures, which are computed from the same rows the page shows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time

import pytest

from core.domain.schedule import FeedSchedule
from core.steps.deliveries import LoadFact, build_deliveries, due_instants

FEED = uuid.uuid4()
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)  # a Friday
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


def at(day: int, hh: int, mm: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hh, mm, tzinfo=UTC)


def sched(version: int = 1, *, frequency="daily", at_time=time(7, 0), grace=60, tz="UTC", weekday=None, day=None,
          since=LONG_AGO, kind="recurring") -> FeedSchedule:
    return FeedSchedule(id=uuid.uuid4(), feed_id=FEED, version=version, kind=kind,
                        frequency=None if kind == "one_time" else frequency, weekday=weekday, day_of_month=day,
                        expected_time=None if kind == "one_time" else at_time, grace_minutes=grace, timezone=tz,
                        notify_owner=True, created_at=since, decided_by="dev-backend-team")


def load(received: datetime, status: str = "released", *, rows: int | None = 10, arrival: str | None = None) -> LoadFact:
    return LoadFact(id=uuid.uuid4(), received_at=received, status=status, arrival_outcome=arrival, rows=rows)


def run(versions, loads, *, start=at(9, 0), now=NOW):
    return build_deliveries(versions, loads, start=start, now=now)


def statuses(result) -> list[tuple[int, str]]:
    return [(r.due_at.day, r.status) for r in result.rows]


def test_each_status_of_a_daily_feed_with_one_hour_grace():
    result = run([sched()], [
        load(at(9, 6, 50)),  # early: on time
        load(at(10, 8, 0)),  # exactly at the end of the grace: on time
        load(at(11, 8, 30)),  # 30 minutes after the grace: late
        # the 12th: nothing came: missing
        load(at(13, 6, 55), "quarantined", arrival="resent_quarantined"),
    ])
    assert statuses(result) == [(9, "on_time"), (10, "on_time"), (11, "late"), (12, "missing"), (13, "resent_quarantined")]
    late = result.rows[2]
    assert late.late_minutes == 30 and late.timing == "late"
    assert result.rows[3].load_id is None and result.rows[3].timing == "missing"


def test_a_held_load_is_held_and_a_released_one_keeps_its_result_beside_its_timing():
    result = run([sched()], [load(at(9, 7, 5), "held"), load(at(10, 9, 0), "released", rows=42),
                             load(at(11, 7, 0), "failed_rolled_back", rows=None)])
    held, released, failed = result.rows[:3]
    assert (held.status, held.result, held.timing) == ("held", "held", "on_time")
    assert (released.status, released.result, released.timing, released.late_minutes, released.rows) == (
        "late", "released", "late", 60, 42)
    assert (failed.status, failed.result) == ("on_time", "failed")


def test_the_delivery_still_inside_its_grace_is_awaited_and_not_a_figure():
    now = at(13, 7, 30)  # the 13th is due 07:00, grace to 08:00
    result = run([sched()], [load(at(9, 7, 0)), load(at(10, 7, 0)), load(at(11, 7, 0)), load(at(12, 7, 0))], now=now)
    assert statuses(result)[-1] == (13, "awaited")
    assert result.figures.expected == 4 and result.figures.next_expected == at(13, 7, 0)
    after = run([sched()], [load(at(9, 7, 0)), load(at(10, 7, 0)), load(at(11, 7, 0)), load(at(12, 7, 0))], now=at(13, 8, 1))
    assert statuses(after)[-1] == (13, "missing") and after.figures.next_expected == at(14, 7, 0)


def test_business_days_skip_the_weekend_and_a_weekend_delivery_is_unexpected():
    # 2026-11-13 is a Friday; the 14th and 15th are the weekend; the 16th a Monday
    now = at(16, 12, 0)
    result = run([sched(frequency="business_days")], [load(at(13, 7, 0)), load(at(15, 7, 0)), load(at(16, 7, 10))],
                 start=at(13, 0), now=now)
    assert statuses(result) == [(13, "on_time"), (16, "on_time")]
    assert [(u.received_at, u.reason) for u in result.unexpected] == [(at(15, 7, 0), "no_delivery_was_due")]
    assert result.figures.unexpected == 1


def test_a_second_delivery_in_one_slot_is_unexpected_and_the_first_is_the_delivery():
    result = run([sched()], [load(at(9, 6, 55)), load(at(9, 7, 30))], start=at(9, 0), now=at(9, 12, 0))
    assert statuses(result) == [(9, "on_time")] and len(result.unexpected) == 1
    assert result.unexpected[0].reason == "second_delivery_for_one_due"


def test_weekly_in_the_feeds_time_zone_across_daylight_saving():
    summer = sched(frequency="weekly", weekday=4, at_time=time(18, 0), tz="Europe/London", grace=30)  # Friday 18:00 BST
    [due] = due_instants([summer], datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 4, tzinfo=UTC))
    assert due.at == datetime(2026, 7, 3, 17, 0, tzinfo=UTC)
    [winter] = due_instants([summer], datetime(2026, 11, 10, tzinfo=UTC), datetime(2026, 11, 14, tzinfo=UTC))
    assert winter.at == datetime(2026, 11, 13, 18, 0, tzinfo=UTC)


def test_monthly_on_a_day_and_on_the_last_day():
    on_15 = sched(frequency="monthly", day="15")
    assert [d.at.date().isoformat() for d in due_instants([on_15], at(1, 0, month=1), at(1, 0, month=4))] == [
        "2026-01-15", "2026-02-15", "2026-03-15"]
    last = sched(frequency="monthly", day="last")
    assert [d.at.date().isoformat() for d in due_instants([last], at(1, 0, month=2), at(1, 0, month=5))] == [
        "2026-02-28", "2026-03-31", "2026-04-30"]


def test_a_schedule_changed_mid_window_uses_the_schedule_in_force_when_each_delivery_was_due():
    # v1: daily 07:00, 0 minutes grace. v2, in force from the 11th at 12:00: business days 09:00, 60 minutes grace.
    v1 = sched(1, grace=0)
    v2 = sched(2, frequency="business_days", at_time=time(9, 0), grace=60, since=at(11, 12, 0))
    result = run([v1, v2], [
        load(at(9, 7, 20)),    # v1: 07:00, no grace: 20 minutes late
        load(at(10, 6, 59)),   # v1: on time
        load(at(11, 7, 0)),    # v1: on time
        load(at(12, 9, 40)),   # v2: 09:00 + 60 grace: on time (v1 would call it late)
        load(at(13, 8, 0)),    # v2: early, on time
    ], start=at(9, 0), now=at(13, 12, 0))
    assert statuses(result) == [(9, "late"), (10, "on_time"), (11, "on_time"), (12, "on_time"), (13, "on_time")]
    assert [r.schedule_version for r in result.rows] == [1, 1, 1, 2, 2]
    assert result.rows[0].late_minutes == 20
    # after the change the weekend is no longer expected: v1 would have expected the 14th and the 15th
    weekend = run([v1, v2], [], start=at(13, 0), now=at(16, 9, 30))
    assert [(r.due_at.day, r.schedule_version) for r in weekend.rows] == [(13, 2), (16, 2)]


def test_a_one_time_schedule_expects_nothing_so_nothing_is_late_or_missing():
    one = sched(kind="one_time")
    result = run([one], [load(at(10, 3, 0))], start=at(1, 0), now=NOW)
    assert result.rows == [] and result.figures.expected == 0 and result.figures.missing == 0 and result.figures.late == 0
    assert [u.reason for u in result.unexpected] == ["no_delivery_was_due"]  # it is listed, never called late or missing


def test_a_one_time_load_that_becomes_recurring_expects_deliveries_only_from_the_change():
    v1 = sched(1, kind="one_time")
    v2 = sched(2, since=at(11, 0, 0))
    result = run([v1, v2], [], start=at(9, 0), now=at(13, 12, 0))
    assert [r.due_at.day for r in result.rows] == [11, 12, 13]
    assert all(r.schedule_version == 2 for r in result.rows)


def test_the_figures_are_counted_from_the_rows_that_are_shown():
    result = run([sched()], [load(at(9, 7, 0)), load(at(10, 7, 0)), load(at(11, 9, 0)), load(at(13, 6, 0))], start=at(9, 0))
    rows = [r for r in result.rows if r.timing != "awaited"]
    f = result.figures
    assert f.expected == len(rows) == 5  # 9, 10, 11, 12, 13
    assert (f.on_time, f.late, f.missing) == (sum(r.timing == "on_time" for r in rows), 1, 1)
    assert f.on_time_pct == pytest.approx(100 * 3 / 5)
    assert f.mean_late_minutes == 60.0  # the 11th: 09:00 is 60 minutes after 08:00
    assert f.streak == 1  # the 13th is on time, the 12th missing
    assert f.next_expected == at(14, 7, 0)


def test_no_deliveries_expected_gives_no_percentage_and_no_streak():
    result = run([sched()], [], start=at(13, 8, 30), now=at(13, 8, 40))
    assert result.rows == [] and result.figures.on_time_pct is None and result.figures.streak == 0
    assert result.figures.mean_late_minutes is None


def test_a_load_that_no_delivery_was_due_for_is_unexpected_and_leaves_the_due_one_missing():
    weekly = sched(frequency="weekly", weekday=4, at_time=time(18, 0), grace=30)  # Fridays 18:00; the 13th is a Friday
    result = run([weekly], [load(at(10, 3, 0))], start=at(9, 0), now=at(13, 19, 0))  # a Tuesday night load
    assert statuses(result) == [(13, "missing")]
    assert [(u.reason, u.received_at) for u in result.unexpected] == [("no_delivery_was_due", at(10, 3, 0))]


def test_the_whole_run_is_deterministic():
    loads = [load(at(9, 7, 0)), load(at(11, 9, 0))]
    first, second = run([sched()], loads), run([sched()], loads)
    assert first.model_dump() == second.model_dump()
