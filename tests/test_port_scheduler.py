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


def test_run_soon_runs_the_function_once_in_the_background(scheduler):
    """Phase 5 plan, assumption 11: once, as soon as possible, not in the caller (run_soon returns first)."""
    import threading

    started, release, calls = threading.Event(), threading.Event(), []

    def work():
        calls.append(threading.current_thread().name)
        started.set()
        release.wait(5)

    scheduler.start()
    scheduler.run_soon("glossary-runs", work)
    assert started.wait(5)
    assert calls[0] != threading.current_thread().name  # another thread: the caller did not wait
    release.set()
    assert scheduler.list_jobs() == []  # nothing is kept: run once, then gone


def test_run_soon_calls_may_overlap(scheduler):
    import threading

    both, gate = threading.Barrier(2, timeout=5), []
    scheduler.start()
    for _ in range(2):
        scheduler.run_soon("glossary-runs", lambda: gate.append(both.wait()))
    for _ in range(50):
        if len(gate) == 2:
            break
        threading.Event().wait(0.1)
    assert sorted(gate) == [0, 1]  # both ran at the same time (the barrier needs two waiting threads)


def test_run_soon_with_a_delay_runs_after_it_not_before(scheduler):
    """Review finding 2: a kick for after a lease. Once, in the background, after delay_seconds."""
    import threading
    import time

    ran, at = threading.Event(), []
    scheduler.start()
    asked = time.monotonic()
    scheduler.run_soon("glossary-runs", lambda: (at.append(time.monotonic()), ran.set()), delay_seconds=0.4)
    assert not ran.wait(0.2)
    assert ran.wait(5) and at[0] - asked >= 0.4


def test_a_timer_that_fires_early_waits_out_the_rest(scheduler, monkeypatch):
    """Phase 6 plan, assumption 29: threading.Timer on Windows can fire up to ~15 ms early. The delayed job
    checks time.monotonic() on firing and waits out the remainder, so 'never before the delay' holds. Here a
    Timer is patched to fire 50 ms early, every time (the real early fire is intermittent)."""
    import threading
    import time

    real_timer = threading.Timer

    class EarlyTimer(real_timer):
        def __init__(self, interval, function, *args, **kwargs):
            super().__init__(max(interval - 0.05, 0.0), function, *args, **kwargs)

    monkeypatch.setattr(threading, "Timer", EarlyTimer)
    ran, at = threading.Event(), []
    scheduler.start()
    asked = time.monotonic()
    scheduler.run_soon("glossary-runs", lambda: (at.append(time.monotonic()), ran.set()), delay_seconds=0.3)
    assert ran.wait(5)
    assert at[0] - asked >= 0.3


@pytest.mark.parametrize("how", ["shutdown", "replaced"])
def test_a_timer_cancelled_while_it_waits_out_the_rest_does_not_run(scheduler, monkeypatch, how):
    """Phase 6 plan, task 6 (checkpoint 1 decision): the timed job re-checks that its timer was not cancelled
    (or shutdown called) after the remainder sleep and before it runs. The Timer is patched to fire 0.45 s
    early, so the job sits in the remainder wait; the test cancels it there."""
    import threading

    real_timer = threading.Timer
    in_the_remainder = threading.Event()

    class EarlyTimer(real_timer):
        def __init__(self, interval, function, *args, **kwargs):
            def entered(*a, **k):
                in_the_remainder.set()
                return function(*a, **k)

            super().__init__(max(interval - 0.45, 0.0), entered, *args, **kwargs)

    monkeypatch.setattr(threading, "Timer", EarlyTimer)
    calls = []
    scheduler.start()
    scheduler.run_soon("glossary-runs", lambda: calls.append("old"), delay_seconds=0.5)
    assert in_the_remainder.wait(5)  # fired early; waiting out the rest
    if how == "shutdown":
        scheduler.shutdown()
    else:  # a later delayed call replaces (cancels) the one that waits; its own timer is not in the remainder yet
        scheduler.run_soon("glossary-runs", lambda: calls.append("new"), delay_seconds=0.5)
    assert not threading.Event().wait(0.6)  # past the old one's due time (0.5 s after run_soon)
    assert "old" not in calls


def test_a_later_delayed_run_replaces_the_one_that_waits(scheduler):
    """One waiting delayed run per job id: many kicks that see a live lease leave one timer, the newest."""
    import threading

    calls, ran = [], threading.Event()
    scheduler.start()
    scheduler.run_soon("glossary-runs", lambda: calls.append("old"), delay_seconds=0.3)
    scheduler.run_soon("glossary-runs", lambda: (calls.append("new"), ran.set()), delay_seconds=0.3)
    assert ran.wait(5)
    assert not threading.Event().wait(0.3) and calls == ["new"]


def test_shutdown_drops_a_delayed_run_that_waits(scheduler):
    import threading

    ran = threading.Event()
    scheduler.start()
    scheduler.run_soon("glossary-runs", ran.set, delay_seconds=0.3)
    scheduler.shutdown()
    assert not ran.wait(0.6)


def test_run_soon_before_start_runs_at_start(scheduler):
    import threading

    ran = threading.Event()
    scheduler.run_soon("glossary-runs", ran.set)
    assert not ran.wait(0.2)
    scheduler.start()
    assert ran.wait(5)


def test_start_and_shutdown_are_safe_to_repeat():
    s = LocalScheduler()
    s.start()
    s.start()
    s.shutdown()
    s.shutdown()
