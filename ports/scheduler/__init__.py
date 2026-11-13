"""Scheduler port. get_scheduler() returns the adapter named by SCHEDULER_PROVIDER (local | azure_functions)."""

from __future__ import annotations

from ports.config import Settings, get_settings
from ports.scheduler.base import JobInfo, JobNotFound, Scheduler

__all__ = ["JobInfo", "JobNotFound", "Scheduler", "get_scheduler"]


def get_scheduler(settings: Settings | None = None) -> Scheduler:
    settings = settings or get_settings()
    if settings.scheduler_provider == "azure_functions":
        from ports.scheduler.azure_functions import AzureFunctionsScheduler

        return AzureFunctionsScheduler()
    from ports.scheduler.local import LocalScheduler

    return LocalScheduler()
