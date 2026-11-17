"""Scheduler port: daily jobs (the custom data product's daily fetch) and one-off background runs
(phase 5: the glossary runs worker). Times are UTC."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class JobNotFound(LookupError):
    """No job has this id."""


class JobInfo(BaseModel):
    job_id: str
    hour: int
    minute: int
    next_run_at: datetime | None  # None until the scheduler has started


def check_time(hour: int, minute: int) -> None:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Invalid daily time {hour:02d}:{minute:02d} (UTC); hour 0-23, minute 0-59")


class Scheduler(Protocol):
    name: str

    def start(self) -> None: ...

    def shutdown(self) -> None: ...

    def schedule_daily(self, job_id: str, func: Callable[[], None], *, hour: int, minute: int = 0) -> JobInfo:
        """Run func every day at hour:minute UTC. A job with the same id is replaced."""
        ...

    def run_now(self, job_id: str) -> None:
        """Run the job once, now. Raises JobNotFound."""
        ...

    def run_soon(self, job_id: str, func: Callable[[], None], *, delay_seconds: float = 0.0) -> None:
        """Run func once, in the background, as soon as possible (phase 5 plan, assumption 11), or after
        delay_seconds. Nothing is kept afterwards. Calls may overlap, so func must be safe to run twice at
        once. One delayed run waits per job id: a later delayed call replaces it."""
        ...

    def remove(self, job_id: str) -> None:
        """Raises JobNotFound."""
        ...

    def list_jobs(self) -> list[JobInfo]: ...
