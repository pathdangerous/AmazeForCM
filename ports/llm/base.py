"""LLMProvider port (CLAUDE.md, LLMProvider contract). Agents call only this; no agent imports an SDK."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, Field, SerializeAsAny


LLMUnavailableReason = Literal["timeout", "rate_limit", "provider_error", "malformed_output"]
LLMRefusedReason = Literal["content_filter", "refusal"]


class LLMUnavailable(Exception):
    """The provider did not answer: a timeout, a rate limit or a provider error. An expected case, not a
    step outcome: nothing is written (no answer, so no proposal_log row) and the API answers 503, in one
    place (apps/api/main.py). Every provider raises this, never its SDK's own errors (independent review,
    fix 4)."""

    def __init__(self, reason: LLMUnavailableReason, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


class LLMRefused(Exception):
    """The model or its content filter refused this input. An expected case: nothing is written and the API
    answers 422, in one place (apps/api/main.py). Retrying the same input will not help."""

    def __init__(self, reason: LLMRefusedReason, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    """One image of a message (phase 7.8, task 2.3): a PNG, base64 encoded."""

    type: Literal["image"] = "image"
    media_type: Literal["image/png"] = "image/png"
    data_b64: str


MessagePart = Annotated[TextPart | ImagePart, Field(discriminator="type")]


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str | list[MessagePart]  # a string, or parts (images and text); every agent but one sends a string

    def text(self) -> str:
        """The message's text: the string, or the last text part (the agent's input block ends the message)."""
        if isinstance(self.content, str):
            return self.content
        texts = [part.text for part in self.content if isinstance(part, TextPart)]
        return texts[-1] if texts else ""


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
