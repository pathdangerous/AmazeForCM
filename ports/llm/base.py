"""LLMProvider port (CLAUDE.md, LLMProvider contract). Agents call only this; no agent imports an SDK."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, SerializeAsAny


LLMUnavailableReason = Literal["timeout", "rate_limit", "provider_error"]


class LLMUnavailable(Exception):
    """The provider did not answer: a timeout, a rate limit or a provider error. An expected case, not a
    step outcome: nothing is written (no answer, so no proposal_log row) and the API answers 503, in one
    place (apps/api/main.py). Every provider raises this, never its SDK's own errors (independent review,
    fix 4)."""

    def __init__(self, reason: LLMUnavailableReason, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMResult(BaseModel):
    output: SerializeAsAny[BaseModel]  # validated against response_schema; serialised with its own fields
    provider: str
    model: str  # deployment name for Azure OpenAI
    model_version: str
    prompt_id: str
    prompt_version: str
    latency_ms: int
    raw_ref: str | None  # pointer to the stored raw response


class LLMProvider(Protocol):
    name: str  # "mock" | "azure_openai"

    def complete(
        self,
        *,
        prompt_id: str,
        prompt_version: str,
        messages: list[Message],
        response_schema: type[BaseModel],
        temperature: float = 0.0,
    ) -> LLMResult: ...
