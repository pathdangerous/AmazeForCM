"""Migration 0039 (phase 7.21, T1; plan docs/plans/phase-7.21.md, 2.2 and 2.3): feed_schedules and delivery_alerts are
append-only, a schedule's version is the previous plus 1, and its shape fits its kind and frequency. By raw SQL on the
test's session; each refusal is checked by its message or constraint name."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.repositories.feeds import FeedRepository

INSERT = ("INSERT INTO feed_schedules (feed_id, version, kind, frequency, weekday, day_of_month, expected_time, "
          "grace_minutes, timezone, notify_owner, decided_by) VALUES (:f, :v, :kind, :freq, :wd, :dom, :t, :g, :tz, true, :by)")


@pytest.fixture
def feed(db_session):
    return FeedRepository(db_session).create_candidate(code="syn_schedule_guards", name="Schedule guards").id


def add(session, feed, *, v=1, kind="recurring", freq="daily", wd=None, dom=None, t="07:00", g=60, tz="UTC", by="dev-operator"):
    session.execute(text(INSERT), {"f": feed, "v": v, "kind": kind, "freq": freq, "wd": wd, "dom": dom, "t": t, "g": g,
                                   "tz": tz, "by": by})


def refused(session, feed, match: str, **over):
    with pytest.raises(DBAPIError, match=match):
        with session.begin_nested():
            add(session, feed, **over)


@pytest.mark.parametrize("fields", [
    dict(freq="daily"), dict(freq="business_days"), dict(freq="weekly", wd=4), dict(freq="monthly", dom="1"),
    dict(freq="monthly", dom="28"), dict(freq="monthly", dom="last"),
    dict(kind="one_time", freq=None, t=None, g=0)])
def test_each_valid_shape_is_stored(db_session, feed, fields):
    add(db_session, feed, **fields)
    assert db_session.execute(text("SELECT count(*) FROM feed_schedules WHERE feed_id = :f"), {"f": feed}).scalar_one() == 1


@pytest.mark.parametrize(("fields", "constraint"), [
    (dict(freq="weekly", wd=None), "weekly_has_weekday"),
    (dict(freq="weekly", wd=7), "weekday"),
    (dict(freq="daily", wd=2), "weekday_only_weekly"),
    (dict(freq="monthly", dom=None), "monthly_has_day"),
    (dict(freq="monthly", dom="29"), "day_of_month"),
    (dict(freq="monthly", dom="0"), "day_of_month"),
    (dict(freq="daily", dom="3"), "day_only_monthly"),
    (dict(kind="one_time", freq="daily", t=None), "one_time_has_no_schedule"),
    (dict(kind="one_time", freq=None, t="07:00"), "one_time_has_no_schedule"),
    (dict(kind="recurring", freq=None), "recurring_has_frequency_and_time"),
    (dict(kind="recurring", t=None), "recurring_has_frequency_and_time"),
    (dict(kind="sometimes"), "kind"),
    (dict(freq="hourly"), "frequency"),
    (dict(g=-1), "grace_minutes"),
    (dict(g=1441), "grace_minutes"),
    (dict(by="  "), "decided_by"),
    (dict(tz=" "), "timezone"),
])
def test_a_shape_that_does_not_fit_is_refused(db_session, feed, fields, constraint):
    refused(db_session, feed, constraint, **fields)


def test_the_first_version_is_1_and_each_next_is_the_previous_plus_1(db_session, feed):
    refused(db_session, feed, "version", v=2)
    add(db_session, feed, v=1)
    refused(db_session, feed, "version", v=3)
    refused(db_session, feed, "version", v=1)
    add(db_session, feed, v=2, freq="business_days")


def test_a_schedule_cannot_be_updated_deleted_or_truncated(db_session, feed):
    add(db_session, feed)
    for sql in ("UPDATE feed_schedules SET grace_minutes = 5", "DELETE FROM feed_schedules", "TRUNCATE feed_schedules"):
        with pytest.raises(DBAPIError, match="refused"):
            with db_session.begin_nested():
                db_session.execute(text(sql))


def test_an_alert_is_once_per_expected_delivery_and_append_only(db_session, feed):
    add(db_session, feed)
    insert = ("INSERT INTO delivery_alerts (feed_id, due_at, schedule_version) "
              "VALUES (:f, '2026-11-02 07:00+00', 1)")
    db_session.execute(text(insert), {"f": feed})
    with pytest.raises(DBAPIError, match="delivery_alerts"):
        with db_session.begin_nested():
            db_session.execute(text(insert), {"f": feed})
    for sql in ("UPDATE delivery_alerts SET schedule_version = 1", "DELETE FROM delivery_alerts", "TRUNCATE delivery_alerts"):
        with pytest.raises(DBAPIError, match="refused"):
            with db_session.begin_nested():
                db_session.execute(text(sql))


def test_deleting_the_feed_deletes_its_schedules_and_alerts(db_session, feed):
    add(db_session, feed)
    db_session.execute(text("INSERT INTO delivery_alerts (feed_id, due_at, schedule_version) "
                            "VALUES (:f, '2026-11-02 07:00+00', 1)"), {"f": feed})
    counts = db_session.execute(text("SELECT amaze_delete_feed(:f, 'dev-operator', 'test')"), {"f": feed}).scalar_one()
    assert counts["feed_schedules"] == 1 and counts["delivery_alerts"] == 1
    assert db_session.execute(text("SELECT count(*) FROM feed_schedules")).scalar_one() == 0
