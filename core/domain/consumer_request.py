"""ConsumerRequest: one "Request this data" and the outcome of its routing checks.

The four outcomes are the routing names in CLAUDE.md. A requirement is linked exactly when the outcome
is raise_requirement. The row is written once and never changed (consumer_requests is append-only);
the requirement's own status is read from the requirement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.domain.common import NonBlank, SnakeName

RoutingOutcome = Literal["reuse", "compose", "create_product", "raise_requirement"]
ROUTING_OUTCOMES: tuple[str, ...] = get_args(RoutingOutcome)


class ConsumerRequestDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requester_id: NonBlank
    query: NonBlank  # the text the requester typed
    purpose: str = ""
    element_keys: list[SnakeName] = Field(default_factory=list)  # the glossary elements the requester ticked
    terms: list[NonBlank] = Field(default_factory=list)  # the unmatched terms the requester ticked
    outcome: RoutingOutcome
    product_codes: list[SnakeName] = Field(default_factory=list)  # the products the outcome names
    requirement_id: UUID | None = None
    result: dict[str, Any]  # the typed result, message and notes as returned to the requester

    @model_validator(mode="after")
    def _consistent(self) -> ConsumerRequestDraft:
        if not self.element_keys and not self.terms:
            raise ValueError("a request names at least one element or term")
        if (self.requirement_id is not None) != (self.outcome == "raise_requirement"):
            raise ValueError("a requirement is linked exactly when the outcome is raise_requirement")
        return self


class ConsumerRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    requester_id: str
    query: str
    purpose: str
    element_keys: list[str]
    terms: list[str]
    outcome: RoutingOutcome
    product_codes: list[str]
    requirement_id: UUID | None
    result: dict[str, Any]
