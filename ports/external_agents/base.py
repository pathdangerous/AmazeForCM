"""ExternalAgent port (phase 7.18): an in-house data agent that may answer one named agent instead of the model provider.

Only agents/base.py calls it. An expected failure is ExternalAgentUnavailable, like LLMUnavailable: nothing is written
(no answer, so no proposal_log row, no proposal) and the API answers 503 (apps/api/main.py)."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

ExternalUnavailableReason = Literal["timeout", "provider_error", "malformed_output", "not_implemented"]


class ExternalAgentUnavailable(Exception):
    """The external agent did not give a usable answer. An expected case, not a 500."""

    def __init__(self, reason: ExternalUnavailableReason, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


class ExternalAgentResult(BaseModel):
    """The answer, and what the proposal log needs to record the call."""

    output: BaseModel  # an instance of the response_schema the call named
    provider: str
    model: str
    model_version: str
    latency_ms: int
    raw_ref: str | None = None


class ExternalAgent(Protocol):
    name: str  # "hexaware"

    def call(self, *, task: str, input: dict[str, Any], response_schema: type[BaseModel]) -> ExternalAgentResult: ...


def validated(response_schema: type[BaseModel], output: Any) -> BaseModel:
    """The output as a response_schema instance, or ExternalAgentUnavailable("malformed_output")."""
    if isinstance(output, response_schema):
        return output
    try:
        return response_schema.model_validate(output)
    except ValidationError as exc:
        raise ExternalAgentUnavailable("malformed_output", f"{response_schema.__name__}: {exc.error_count()} errors") from None
