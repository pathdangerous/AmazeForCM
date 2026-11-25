"""Phase 7.21, T3: deliveries read from the database, the typed results, and the once-per-delivery missing alert.

The matching itself is in tests/test_deliveries_match.py (pure). Here: the loads, the certification rows, a schedule changed in
the middle of the window (a version row inserted with an explicit past created_at, which the append-only table allows),
a one-time feed, a feed with no schedule, the alerts, and that no model is called."""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest
from sqlalchemy import func, select, text

from core.domain.schedule import ScheduleInput
from core.repositories.feeds import FeedRepository
from core.repositories.tables import DeliveryAlertRow, ProposalLogEntry
from core.steps.deliveries import (DeliveriesShown, OneTimeShown, ScheduleNotSet, deliveries_for_feed,
                                   record_missing_alerts)
from core.steps.schedule import set_schedule
from tests.configured import certified_load
from tests.conftest import dev_user_for
from tests.onboarded import registered_feed

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)  # a Friday
DAILY = ScheduleInput(kind="recurring", frequency="daily", expected_time=time(7, 0), grace_minutes=60)


def at(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 11, day, hh, mm, tzinfo=UTC)


def make_feed(session, code="syn_deliveries", *, schedule: ScheduleInput | None = DAILY):
    feed = FeedRepository(session).create_candidate(code=code, name=code.replace("_", " ").capitalize())
    registered_feed(session, feed.id, scheduled=False)
    if schedule is not None:
        set_schedule(session, feed_id=feed.id, schedule=schedule, user=dev_user_for("backend_team"))
    return feed.id


def deliver(session, feed_id, when: datetime, status="released", rows=10):
    return certified_load(session, feed_id, status, rows=rows, received_at=when)


def test_a_recurring_feed_shows_each_expected_delivery_with_its_load_and_the_figures_equal_the_rows(db_session):
    feed = make_feed(db_session)
    deliver(db_session, feed, at(9, 6, 55), rows=10)
    deliver(db_session, feed, at(10, 9, 0), rows=20)  # an hour after the grace ended
    deliver(db_session, feed, at(12, 7, 30), "held", rows=30)  # 11th: nothing
    shown = deliveries_for_feed(db_session, feed, window="30", now=NOW)
    assert isinstance(shown, DeliveriesShown) and shown.schedule.version == 1
    assert [(r.due_at.day, r.status, r.result, r.rows) for r in shown.rows] == [
        (9, "on_time", "released", 10), (10, "late", "released", 20), (11, "missing", None, None),
        (12, "held", "held", 30), (13, "missing", None, None)]
    f = shown.figures
    assert (f.expected, f.on_time, f.late, f.missing, f.on_time_pct, f.mean_late_minutes) == (5, 2, 1, 2, 40.0, 60.0)
    assert f.expected == len([r for r in shown.rows if r.timing != "awaited"])
    assert f.next_expected == at(14, 7, 0) and f.streak == 0 and f.unexpected == 0
    assert shown.live_since == at(9, 6, 55)


def test_the_window_cuts_the_rows_but_never_goes_before_the_feed_loaded(db_session):
    feed = make_feed(db_session)
    deliver(db_session, feed, at(9, 7, 0))
    assert len(deliveries_for_feed(db_session, feed, window="90", now=NOW).rows) == 5
    assert len(deliveries_for_feed(db_session, feed, window="all", now=NOW).rows) == 5
    assert len(deliveries_for_feed(db_session, feed, window="30", now=datetime(2026, 12, 20, 12, tzinfo=UTC)).rows) == 30


def test_a_schedule_changed_mid_window_judges_each_delivery_by_the_schedule_in_force_when_it_was_due(db_session):
    feed = make_feed(db_session, schedule=ScheduleInput(kind="recurring", frequency="daily", expected_time=time(7, 0)))
    # version 2 is in force from the 11th 12:00: business days at 09:00, an hour of grace. The row is inserted with that
    # created_at: the table is append-only, but a row may be written for a past moment.
    db_session.execute(text(
        "INSERT INTO feed_schedules (feed_id, version, kind, frequency, expected_time, grace_minutes, timezone, decided_by, "
        "created_at) VALUES (:f, 2, 'recurring', 'business_days', '09:00', 60, 'UTC', 'dev-backend-team', :t)"),
        {"f": feed, "t": at(11, 12, 0)})
    deliver(db_session, feed, at(9, 7, 20))   # version 1: 07:00, no grace: late by 20
    deliver(db_session, feed, at(10, 6, 59))
    deliver(db_session, feed, at(11, 7, 0))
    deliver(db_session, feed, at(12, 9, 40))  # version 2: 09:00 + 60: on time (version 1 would say late)
    shown = deliveries_for_feed(db_session, feed, window="30", now=at(13, 12, 0))
    assert [(r.due_at.day, r.schedule_version, r.status) for r in shown.rows] == [
        (9, 1, "late"), (10, 1, "on_time"), (11, 1, "on_time"), (12, 2, "on_time"), (13, 2, "missing")]
    assert shown.schedule.version == 2 and shown.rows[0].late_minutes == 20


def test_a_one_time_feed_shows_its_loads_with_the_label_and_never_late_or_missing(db_session):
    feed = make_feed(db_session, "syn_one_time", schedule=ScheduleInput(kind="one_time"))
    deliver(db_session, feed, at(10, 3, 0), rows=7)
    shown = deliveries_for_feed(db_session, feed, now=NOW)
    assert isinstance(shown, OneTimeShown) and shown.label == "One-time load"
    assert sorted((load.purpose, load.status, load.rows) for load in shown.loads) == [
        ("go_live_test", "loaded", None), ("scheduled", "released", 7)]  # the go-live's own test load is listed too
    assert not hasattr(shown, "figures")


def test_a_feed_without_a_schedule_is_schedule_not_set(db_session):
    feed = make_feed(db_session, "syn_no_schedule", schedule=None)
    shown = deliveries_for_feed(db_session, feed, now=NOW)
    assert isinstance(shown, ScheduleNotSet) and shown.outcome == "schedule_not_set"


def test_a_recurring_feed_that_has_not_loaded_expects_nothing(db_session):
    feed = make_feed(db_session, "syn_not_loaded")
    shown = deliveries_for_feed(db_session, feed, now=NOW)  # no go-live and no scheduled load
    assert isinstance(shown, DeliveriesShown) and shown.live_since is None and shown.rows == []
    assert shown.figures.expected == 0 and shown.figures.on_time_pct is None


class Recording:
    name = "recording"

    def __init__(self):
        self.sent: list[dict] = []

    def notify(self, *, recipient, subject, body, link=None):
        self.sent.append({"recipient": recipient, "subject": subject, "body": body, "link": link})
        return "id"


def test_each_missing_delivery_tells_the_owner_once(db_session):
    feed = make_feed(db_session, "syn_alerts")
    deliver(db_session, feed, at(9, 6, 55))
    notifier = Recording()
    first = record_missing_alerts(db_session, feed_id=feed, notifier=notifier, now=NOW)
    assert [a.outcome for a in first] == ["late_alert"] * 4  # the 10th, 11th, 12th and 13th
    assert [s["recipient"] for s in notifier.sent] == ["dev-data-owner"] * 4
    assert all(s["subject"] == "Delivery missing: syn_alerts" and s["link"] == f"#/feed/{feed}" for s in notifier.sent)
    stored = db_session.scalars(select(DeliveryAlertRow.due_at).where(DeliveryAlertRow.feed_id == feed)).all()
    assert sorted(stored) == [at(10, 7), at(11, 7), at(12, 7), at(13, 7)]
    again = record_missing_alerts(db_session, feed_id=feed, notifier=notifier, now=at(13, 15, 0))
    assert again == [] and len(notifier.sent) == 4  # once for each expected delivery
    later = record_missing_alerts(db_session, feed_id=feed, notifier=notifier, now=at(14, 8, 30))
    assert len(later) == 1 and len(notifier.sent) == 5  # only the 14th is new


def test_no_alert_when_the_schedule_says_not_to_tell_the_owner_or_for_a_one_time_feed(db_session):
    quiet = make_feed(db_session, "syn_quiet", schedule=DAILY.model_copy(update={"notify_owner": False}))
    one_time = make_feed(db_session, "syn_once", schedule=ScheduleInput(kind="one_time"))
    deliver(db_session, quiet, at(9, 6, 55))
    deliver(db_session, one_time, at(9, 6, 55))
    notifier = Recording()
    assert record_missing_alerts(db_session, feed_id=quiet, notifier=notifier, now=NOW) == []
    assert record_missing_alerts(db_session, feed_id=one_time, notifier=notifier, now=NOW) == []
    assert notifier.sent == []


def test_a_missing_delivery_older_than_a_week_is_shown_but_the_owner_is_not_told_now(db_session):
    feed = make_feed(db_session, "syn_old_missing")
    deliver(db_session, feed, at(1, 6, 55))
    notifier = Recording()
    sent = record_missing_alerts(db_session, feed_id=feed, notifier=notifier, now=NOW)  # due from the 1st to the 13th
    assert len(sent) == 7 and "2026-11-07 07:00" in sent[0].detail  # the 2nd to the 6th ended more than 7 days ago
    assert deliveries_for_feed(db_session, feed, now=NOW).figures.missing == 12


@pytest.mark.parametrize("call", ["deliveries", "alerts"])
def test_no_model_is_called(db_session, call):
    feed = make_feed(db_session, "syn_no_model")
    deliver(db_session, feed, at(9, 6, 55))
    before = db_session.scalar(select(func.count()).select_from(ProposalLogEntry))
    if call == "deliveries":
        deliveries_for_feed(db_session, feed, now=NOW)
    else:
        record_missing_alerts(db_session, feed_id=feed, notifier=Recording(), now=NOW)
    assert db_session.scalar(select(func.count()).select_from(ProposalLogEntry)) == before
