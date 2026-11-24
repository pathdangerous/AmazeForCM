"""ExternalAgent port (phase 7.18). get_external_agent() returns the adapter named by EXTERNAL_AGENT_PROVIDER
(none | hexaware); none gives no adapter: nothing is ever called. Only agents/base.py uses this package."""

from __future__ import annotations

from ports.config import Settings, get_settings
from ports.external_agents.base import (ExternalAgent, ExternalAgentResult, ExternalAgentUnavailable, validated)

__all__ = ["ExternalAgent", "ExternalAgentResult", "ExternalAgentUnavailable", "get_external_agent", "validated"]


def get_external_agent(settings: Settings | None = None) -> ExternalAgent | None:
    settings = settings or get_settings()
    if settings.external_agent_provider == "hexaware":
        from ports.external_agents.hexaware import HexawareExternalAgent

        return HexawareExternalAgent()
    return None
