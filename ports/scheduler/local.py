"""Local Scheduler (SCHEDULER_PROVIDER=local): APScheduler 3 inside the API process.

Jobs are kept in memory only: an API restart loses them (plan risk 1; phase 3 must persist them
or re-register them from the database at start-up).
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ports.scheduler.base import JobInfo, JobNotFound, check_time

log = logging.getLogger(__name__)


class LocalScheduler:
    name = "local"

    def __init__(self) -> None:
        self._scheduler = BackgroundScheduler(timezone=UTC)
        self._times: dict[str, tuple[int, int]] = {}
        self._soon_lock = threading.Lock()
        self._soon_pending: list[tuple[str, Callable[[], None]]] = []

    def start(self) -> None:
        with self._soon_lock:
            if not self._scheduler.running:
                self._scheduler.start()
            pending, self._soon_pending = self._soon_pending, []
        for job_id, guarded in pending:
            threading.Thread(target=guarded, name=f"{job_id}:{uuid.uuid4().hex[:8]}", daemon=True).start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def schedule_daily(self, job_id: str, func: Callable[[], None], *, hour: int, minute: int = 0) -> JobInfo:
        check_time(hour, minute)
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.remove_job(job_id)
        self._scheduler.add_job(func, CronTrigger(hour=hour, minute=minute, timezone=UTC), id=job_id)
        self._times[job_id] = (hour, minute)
        return self._info(job_id)

    def run_now(self, job_id: str) -> None:
        self._job(job_id).func()

    def run_soon(self, job_id: str, func: Callable[[], None]) -> None:
        """Each call on a daemon thread of its own, at once (or at start, if called before), so calls may
        overlap. Not an APScheduler job: APScheduler 3 sets its state to stopped before it takes its job-store
        lock in shutdown(), so a one-off job finishing then raises JobLookupError in its thread (phase 5 plan,
        section 17). An exception in func is logged, never raised in the thread."""

        def guarded() -> None:
            try:
                func()
            except Exception:
                log.exception("background run %s failed", job_id)

        with self._soon_lock:
            if not self._scheduler.running:
                self._soon_pending.append((job_id, guarded))
                return
        threading.Thread(target=guarded, name=f"{job_id}:{uuid.uuid4().hex[:8]}", daemon=True).start()

    def remove(self, job_id: str) -> None:
        self._job(job_id)
        self._scheduler.remove_job(job_id)
        del self._times[job_id]

    def list_jobs(self) -> list[JobInfo]:
        return sorted((self._info(job.id) for job in self._scheduler.get_jobs() if job.id in self._times),
                      key=lambda j: j.job_id)  # daily jobs only; run_soon jobs are not kept

    def _job(self, job_id: str):
        job = self._scheduler.get_job(job_id)
        if job is None:
            raise JobNotFound(f"No scheduled job {job_id!r}")
        return job

    def _info(self, job_id: str) -> JobInfo:
        hour, minute = self._times[job_id]
        next_run = getattr(self._job(job_id), "next_run_time", None)  # absent until the scheduler starts
        return JobInfo(job_id=job_id, hour=hour, minute=minute, next_run_at=next_run)
