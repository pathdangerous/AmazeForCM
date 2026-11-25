"""Adapter selection. Reads the six *_PROVIDER selectors, LOCAL_BLOB_ROOT, SOURCE_CATALOGUE and
SOURCE_HTTP_ALLOWED_HOSTS from the process environment, then from .env in the repository root. The
local adapters are the defaults.

Secrets such as DATABASE_URL are not read here; they go through the Secrets port.
"""

from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal, get_args

from dotenv import dotenv_values
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

AZURE_NOT_IMPLEMENTED = "Azure adapter: implement after move"


class ConfigError(ValueError):
    """A configuration value is missing or not one of the allowed values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=DEFAULT_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    llm_provider: Literal["mock", "azure_openai"] = "mock"
    storage_provider: Literal["local", "azure_blob"] = "local"
    scheduler_provider: Literal["local", "azure_functions"] = "local"
    identity_provider: Literal["dev", "entra_id"] = "dev"
    secrets_provider: Literal["env", "key_vault"] = "env"
    notify_provider: Literal["inapp", "email"] = "inapp"
    # Phase 7.17: where a feed's generated code is committed. The local adapter is a git repository of its own, never the
    # project's (git-ignored under data/).
    pipeline_provider: Literal["local", "azure_devops"] = "local"
    pipeline_repo_root: Path = REPO_ROOT / "data" / "pipeline-repo"
    # Phase 7.18: an in-house data agent may answer ONE named agent (EXTERNAL_AGENT_FOR, an Agent.name; empty = none). "none"
    # is never called.
    external_agent_provider: Literal["none", "hexaware"] = "none"
    external_agent_for: str = ""
    local_blob_root: Path = Path("./data/blob")
    # Custom data product (phase 3): the synthetic source catalogue, and the only hosts the HTTP
    # connector may reach (comma-separated). This machine only until the Azure move.
    source_catalogue: Path = REPO_ROOT / "fixtures" / "sources" / "catalogue.json"
    source_http_allowed_hosts: str = "127.0.0.1,localhost"
    # Phase 3b: a selected source that did not answer on this many UTC days in a row is released.
    source_down_release_days: int = Field(default=3, ge=1)
    # Phase 5: a glossary run left running this long by a worker that died is claimed again.
    glossary_run_lease_seconds: int = Field(default=300, ge=1)
    # Phase 6: the longest one validation rule may run (the DuckDB sandbox interrupts it), and the number of days
    # after go-live in which every scheduled load is reported to the owner and operations (hypercare).
    rule_timeout_seconds: int = Field(default=5, ge=1)
    # Phase 7.20: the most rows one page of "click a rule" may hold (a larger size is cut to it).
    rule_rows_max_size: int = Field(default=200, ge=1)
    hypercare_days: int = Field(default=10, ge=1)
    # Phase 7: a load left received this long by a process that died is abandoned before the next load (step 12).
    load_stale_minutes: int = Field(default=30, ge=1)
    # Phase 7: a scheduled load whose trust score (100 x unflagged rows / rows) is at or above this is released
    # (step 15); below it the load is held and its consumers are warned.
    trust_release_threshold: Decimal = Field(default=Decimal("90"), ge=1, le=100)
    # Phase 7.15: a recurring failure pattern needs this many failing loads in this many days (UTC); the report job runs
    # at REPORT_HOUR (UTC) and builds yesterday's daily report, and on the 1st last month's monthly report.
    pattern_min_loads: int = Field(default=3, ge=2)
    pattern_window_days: int = Field(default=90, ge=7)
    report_hour: int = Field(default=6, ge=0, le=23)
    # Phase 7 (plan risk 6): false registers no daily job (the glossary runs worker still runs); the e2e API sets it
    # so the 06:00 UTC fetch cannot fire during a Playwright run.
    daily_job_enabled: bool = True
    # Phase 7.8: the per-call timeout of the real model adapter, and the longest Retry-After it waits on an HTTP 429
    # before it retries.
    llm_timeout_seconds: int = Field(default=60, ge=1)
    llm_retry_after_cap_seconds: int = Field(default=20, ge=0)
    # Phase 7.8, D2: false lets one operator hold every role and approve every step; true keeps steward then
    # checker, owner-only go-live and no same approver twice. The database row is what the guards read; the API
    # writes it at start-up (apps/api/main.py).
    require_four_eyes: bool = False
    # Phase 7.8: the cap on model calls in one onboarding (enforced by core/steps/onboarding.py, task 5.3).
    max_model_calls_per_onboarding: int = Field(default=60, ge=1)
    # Phase 7.8, S5 (assumption 35): a proposal whose confidence is at or above this, and is no tie, is shown as
    # pre-accepted in the onboarding workspace; below it a person decides it. The operator still clicks Approve & next.
    auto_accept_confidence: float = Field(default=0.85, ge=0, le=1)
    # Phase 7.8, S5 (task 5.4): an onboarding job left running this long by a worker that died is claimed again.
    onboarding_job_lease_seconds: int = Field(default=300, ge=1)
    # Phase 7.8, S2: the largest file the upload endpoint takes (UPLOAD_MAX_MB, in MB of 1024 x 1024 bytes).
    upload_max_mb: int = Field(default=50, ge=1)
    # Phase 7.8, S4 (decision D5): the watched drop folder of the uploaded feeds, <root>/<feed code>/ (DROPZONE_ROOT).
    dropzone_root: Path = REPO_ROOT / "data" / "dropzone"
    # Phase 7.8, D3: true sends at most 5 distinct non-empty values per column to a model (core/steps/samples.py);
    # false (the default) sends none.
    send_sample_values: bool = False

    @field_validator("source_catalogue", "dropzone_root", "pipeline_repo_root")
    @classmethod
    def _path_from_the_repository(cls, path: Path) -> Path:
        """A relative path (as in .env.example) is read from the repository, not the current folder."""
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    def allowed_source_hosts(self) -> tuple[str, ...]:
        return tuple(h.strip().lower() for h in self.source_http_allowed_hosts.split(",") if h.strip())


def load_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> Settings:
    """Build Settings, turning validation errors into one readable ConfigError."""
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            field = str(error["loc"][0])
            allowed = get_args(Settings.model_fields[field].annotation)
            hint = f"; use one of: {', '.join(allowed)}" if allowed else ""
            problems.append(f"{field.upper()}={error.get('input')!r} is not allowed{hint}")
        raise ConfigError("Invalid configuration: " + " | ".join(problems)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def read_env(
    keys: Iterable[str] | None = None,
    *,
    env_file: Path | None = DEFAULT_ENV_FILE,
    include_process_env: bool = True,
) -> dict[str, str]:
    """Values from env_file, overridden by the process environment. Empty values are kept as "".

    With keys, returns exactly those keys (missing ones as ""); without, everything found.
    """
    values: dict[str, str] = {}
    if env_file is not None and Path(env_file).is_file():
        values.update({k: v or "" for k, v in dotenv_values(env_file, encoding="utf-8").items()})
    if include_process_env:
        values.update(os.environ)
    if keys is None:
        return values
    return {key: values.get(key, "") for key in keys}
