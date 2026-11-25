"""Phase 7.21, T2: a schedule's setting and every change is a row of the audit view (who, when, what it became)."""

from __future__ import annotations

from datetime import UTC, datetime, time

from core.domain.schedule import ScheduleInput
from core.repositories.feeds import FeedRepository
from core.steps.audit import audit_csv, audit_rows
from core.steps.schedule import set_schedule
from tests.conftest import dev_user_for

MONTH = f"{datetime.now(UTC):%Y-%m}"
FIRST = ScheduleInput(kind="recurring", frequency="business_days", expected_time=time(7, 0), grace_minutes=60)


def test_setting_and_changing_a_schedule_are_audit_rows(db_session):
    feed = FeedRepository(db_session).create_candidate(code="syn_audit_schedule", name="Audit schedule")
    other = FeedRepository(db_session).create_candidate(code="syn_audit_other", name="Other")
    set_schedule(db_session, feed_id=feed.id, schedule=FIRST, user=dev_user_for("backend_team"))
    set_schedule(db_session, feed_id=feed.id, schedule=FIRST.model_copy(update={"expected_time": time(8, 0)}),
                 user=dev_user_for("data_owner"))
    set_schedule(db_session, feed_id=other.id, schedule=ScheduleInput(kind="one_time"), user=dev_user_for("backend_team"))

    rows = [r for r in audit_rows(db_session, month=MONTH, feed_id=feed.id) if r.kind == "schedule"]
    assert [(r.decision, r.who, r.item, r.feed_code, r.by) for r in rows] == [
        ("set", dev_user_for("backend_team").id, "schedule", "syn_audit_schedule", "manual"),
        ("changed", dev_user_for("data_owner").id, "schedule", "syn_audit_schedule", "manual")]
    assert "on business days at 08:00 UTC, 60 min grace" in rows[1].summary and "version 2" in rows[1].summary

    everyone = [r for r in audit_rows(db_session, month=MONTH) if r.kind == "schedule"]
    assert {r.feed_code for r in everyone} == {"syn_audit_schedule", "syn_audit_other"}
    mine = audit_rows(db_session, month=MONTH, person=dev_user_for("data_owner").id)
    assert [r.decision for r in mine if r.kind == "schedule"] == ["changed"]
    assert "One-time load" in audit_csv(everyone)
