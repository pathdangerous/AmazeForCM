"""Hexaware in-house data agents. STUB: implement after their API is known.

No network and no HTTP client until then. The keys below are read in the constructor and listed in .env.example."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ports.config import DEFAULT_ENV_FILE, read_env
from ports.external_agents.base import ExternalAgentResult

EXTERNAL_NOT_IMPLEMENTED = "External agent: implement after API is known"


class HexawareExternalAgent:
    name = "hexaware"
    is_stub = True  # /api/health reports this adapter as unavailable
    ENV_KEYS = ("EXTERNAL_AGENT_BASE_URL", "EXTERNAL_AGENT_API_KEY", "EXTERNAL_AGENT_TIMEOUT_SECONDS")

    def __init__(self, env_file: Path | None = DEFAULT_ENV_FILE):
        self.config = read_env(self.ENV_KEYS, env_file=env_file)

    def call(self, *, task: str, input: dict[str, Any], response_schema: type[BaseModel]) -> ExternalAgentResult:
        raise NotImplementedError(EXTERNAL_NOT_IMPLEMENTED)
