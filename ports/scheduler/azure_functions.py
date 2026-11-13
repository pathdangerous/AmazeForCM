"""Azure Functions timer-trigger Scheduler. STUB: implement after the move to the office laptop.

Plan for the implementation: each daily job becomes a timer-triggered function in the Function App
AZURE_FUNCTIONS_APP (NCRONTAB "0 <minute> <hour> * * *", UTC). Job definitions must live in the
database, because the function runs outside the API process. Check Azure Functions support for
Python 3.14 first (plan risk 2).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ports.config import AZURE_NOT_IMPLEMENTED, DEFAULT_ENV_FILE, read_env
from ports.scheduler.base import JobInfo


class AzureFunctionsScheduler:
    name = "azure_functions"
    is_stub = True  # /api/health reports this adapter as unavailable; the API does not start it
    ENV_KEYS = ("AZURE_FUNCTIONS_APP",)

    def __init__(self, env_file: Path | None = DEFAULT_ENV_FILE):
        self.config = read_env(self.ENV_KEYS, env_file=env_file)

    def start(self) -> None:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)

    def shutdown(self) -> None:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)

    def schedule_daily(self, job_id: str, func: Callable[[], None], *, hour: int, minute: int = 0) -> JobInfo:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)

    def run_now(self, job_id: str) -> None:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)

    def remove(self, job_id: str) -> None:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)

    def list_jobs(self) -> list[JobInfo]:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)
