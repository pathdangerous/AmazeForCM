"""Phase 7.21, T1 (plan docs/plans/phase-7.21.md, 2.1 to 2.4): setting and changing a feed's schedule. A schedule is a
person's setting: versioned, who and when kept, no model call."""

from __future__ import annotations

from datetime import time

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from core.domain.schedule import ScheduleInput
from core.repositories.errors import FeedNotFound
from core.repositories.feeds import FeedRepository
from core.repositories.schedules import FeedScheduleRepository
from core.repositories.tables import ProposalLogEntry
from core.steps.schedule import ScheduleSet, ScheduleUnchanged, set_schedule
from tests.conftest import dev_user_for

DAILY = ScheduleInput(kind="recurring", frequency="business_days", expected_time=time(7, 0), grace_minutes=60)


@pytest.fixture
def feed(db_session):
    return FeedRepository(db_session).create_candidate(code="syn_schedule_step", name="Schedule step").id


def test_the_first_schedule_is_version_1_with_who_decided_it(db_session, feed):
    result = set_schedule(db_session, feed_id=feed, schedule=DAILY, user=dev_user_for("backend_team"))
    assert isinstance(result, ScheduleSet)
    assert (result.schedule.version, result.schedule.decided_by) == (1, dev_user_for("backend_team").id)
    assert (result.schedule.frequency, result.schedule.expected_time, result.schedule.grace_minutes) == (
        "business_days", time(7, 0), 60)
    assert result.schedule.created_at is not None


def test_the_same_schedule_again_is_unchanged_and_makes_no_version(db_session, feed):
    set_schedule(db_session, feed_id=feed, schedule=DAILY, user=dev_user_for("backend_team"))
    again = set_schedule(db_session, feed_id=feed, schedule=DAILY, user=dev_user_for("backend_team"))
    assert isinstance(again, ScheduleUnchanged) and again.schedule.version == 1
    assert len(FeedScheduleRepository(db_session).list_for_feed(feed)) == 1


def test_a_change_is_a_new_version_and_the_old_one_stays(db_session, feed):
    set_schedule(db_session, feed_id=feed, schedule=DAILY, user=dev_user_for("backend_team"))
    changed = set_schedule(db_session, feed_id=feed, user=dev_user_for("data_owner"),
                           schedule=DAILY.model_copy(update={"expected_time": time(8, 30), "grace_minutes": 15}))
    assert isinstance(changed, ScheduleSet) and changed.schedule.version == 2
    versions = FeedScheduleRepository(db_session).list_for_feed(feed)
    assert [(v.version, v.expected_time, v.decided_by) for v in versions] == [
        (1, time(7, 0), dev_user_for("backend_team").id), (2, time(8, 30), dev_user_for("data_owner").id)]
    assert FeedScheduleRepository(db_session).current(feed).version == 2


def test_a_one_time_load_is_a_schedule_with_no_frequency(db_session, feed):
    result = set_schedule(db_session, feed_id=feed, schedule=ScheduleInput(kind="one_time"), user=dev_user_for("backend_team"))
    assert result.schedule.kind == "one_time" and result.schedule.frequency is None and result.schedule.expected_time is None


@pytest.mark.parametrize("fields", [
    dict(kind="recurring", frequency="weekly", expected_time=time(7, 0)),  # no weekday
    dict(kind="recurring", frequency="monthly", expected_time=time(7, 0)),  # no day
    dict(kind="recurring", frequency="monthly", day_of_month="29", expected_time=time(7, 0)),
    dict(kind="recurring", frequency="daily", weekday=2, expected_time=time(7, 0)),
    dict(kind="recurring", frequency="daily"),  # no time
    dict(kind="recurring", expected_time=time(7, 0)),  # no frequency
    dict(kind="one_time", frequency="daily"),
    dict(kind="recurring", frequency="daily", expected_time=time(7, 0), timezone="Mars/Olympus"),
    dict(kind="recurring", frequency="daily", expected_time=time(7, 0), grace_minutes=-1),
])
def test_a_schedule_that_does_not_fit_is_refused_before_anything_is_written(db_session, feed, fields):
    with pytest.raises(ValidationError):
        ScheduleInput(**fields)
    assert FeedScheduleRepository(db_session).list_for_feed(feed) == []


def test_an_unknown_feed_is_feed_not_found(db_session):
    import uuid

    with pytest.raises(FeedNotFound):
        set_schedule(db_session, feed_id=uuid.uuid4(), schedule=DAILY, user=dev_user_for("backend_team"))


def test_no_model_is_called(db_session, feed):
    before = db_session.scalar(select(func.count()).select_from(ProposalLogEntry))
    set_schedule(db_session, feed_id=feed, schedule=DAILY, user=dev_user_for("backend_team"))
    assert db_session.scalar(select(func.count()).select_from(ProposalLogEntry)) == before
