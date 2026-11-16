"""Adapter selection. Reads the six *_PROVIDER selectors, LOCAL_BLOB_ROOT, SOURCE_CATALOGUE and
SOURCE_HTTP_ALLOWED_HOSTS from the process environment, then from .env in the repository root. The
local adapters are the defaults.

Secrets such as DATABASE_URL are not read here; they go through the Secrets port.
"""

from __future__ import annotations

import os
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
    local_blob_root: Path = Path("./data/blob")
    # Custom data product (phase 3): the synthetic source catalogue, and the only hosts the HTTP
    # connector may reach (comma-separated). This machine only until the Azure move.
    source_catalogue: Path = REPO_ROOT / "fixtures" / "sources" / "catalogue.json"
    source_http_allowed_hosts: str = "127.0.0.1,localhost"
    # Phase 3b: a selected source that did not answer on this many UTC days in a row is released.
    source_down_release_days: int = Field(default=3, ge=1)
    # Phase 5: a glossary run left running this long by a worker that died is claimed again.
    glossary_run_lease_seconds: int = Field(default=300, ge=1)

    @field_validator("source_catalogue")
    @classmethod
    def _catalogue_from_the_repository(cls, path: Path) -> Path:
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
