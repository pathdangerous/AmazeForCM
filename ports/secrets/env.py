"""Local Secrets adapter: the .env file, overridden by the process environment."""

from __future__ import annotations

from pathlib import Path

from ports.config import DEFAULT_ENV_FILE, read_env
from ports.secrets.base import SecretNotFound


class EnvSecrets:
    name = "env"

    def __init__(self, env_file: Path | None = DEFAULT_ENV_FILE):
        self._env_file = env_file

    def get(self, key: str) -> str:
        value = read_env([key], env_file=self._env_file)[key]
        if not value:
            raise SecretNotFound(f"Secret {key} is not set (environment or {self._env_file})")
        return value
