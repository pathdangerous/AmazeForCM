"""LLM port. get_llm_provider() returns the adapter named by LLM_PROVIDER (mock | azure_openai)."""

from __future__ import annotations

from ports.config import Settings, get_settings
from ports.llm.base import LLMProvider, LLMResult, Message

__all__ = ["LLMProvider", "LLMResult", "Message", "get_llm_provider"]


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if settings.llm_provider == "azure_openai":
        from ports.llm.azure_openai import AzureOpenAIProvider

        return AzureOpenAIProvider()
    from ports.llm.mock import MockProvider

    return MockProvider()
