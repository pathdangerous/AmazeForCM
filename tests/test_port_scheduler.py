"""Scheduler port, local adapter: APScheduler inside the API process, daily jobs in UTC, kept in memory."""

from datetime import UTC, datetime

import pytest

from ports.scheduler.base import JobInfo, JobNotFound
from ports.scheduler.local import LocalScheduler


@pytest.fixture
def scheduler():
    s = LocalScheduler()
    yield s
    s.shutdown()


def test_schedule_daily_and_list(scheduler):
    info = scheduler.schedule_daily("fetch-feed-a", lambda: None, hour=2, minute=30)
    assert isinstance(info, JobInfo)
    assert (info.job_id, info.hour, info.minute) == ("fetch-feed-a", 2, 30)
    assert [j.job_id for j in scheduler.list_jobs()] == ["fetch-feed-a"]


def test_run_now_runs_the_job_in_the_caller(scheduler):
    calls = []
    scheduler.schedule_daily("job", lambda: calls.append(1), hour=0)
    scheduler.run_now("job")
    assert calls == [1]


def test_same_id_replaces_the_job(scheduler):
    calls = []
    scheduler.schedule_daily("job", lambda: calls.append("old"), hour=1)
    scheduler.schedule_daily("job", lambda: calls.append("new"), hour=3)
    assert [(j.job_id, j.hour) for j in scheduler.list_jobs()] == [("job", 3)]
    scheduler.run_now("job")
    assert calls == ["new"]


def test_remove(scheduler):
    scheduler.schedule_daily("job", lambda: None, hour=1)
    scheduler.remove("job")
    assert scheduler.list_jobs() == []


@pytest.mark.parametrize("action", ["run_now", "remove"])
def test_unknown_job(scheduler, action):
    with pytest.raises(JobNotFound, match="no-such-job"):
        getattr(scheduler, action)("no-such-job")


@pytest.mark.parametrize(("hour", "minute"), [(24, 0), (-1, 0), (0, 60), (0, -1)])
def test_invalid_time(scheduler, hour, minute):
    with pytest.raises(ValueError):
        scheduler.schedule_daily("job", lambda: None, hour=hour, minute=minute)


def test_next_run_is_in_the_future_at_the_utc_time_after_start(scheduler):
    scheduler.schedule_daily("job", lambda: None, hour=4, minute=15)
    scheduler.start()
    (info,) = scheduler.list_jobs()
    assert info.next_run_at is not None
    assert info.next_run_at > datetime.now(UTC)
    assert info.next_run_at.astimezone(UTC).hour == 4
    assert info.next_run_at.astimezone(UTC).minute == 15


def test_start_and_shutdown_are_safe_to_repeat():
    s = LocalScheduler()
    s.start()
    s.start()
    s.shutdown()
    s.shutdown()
