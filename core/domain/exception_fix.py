"""The ExceptionFixAgent's input and output (phase 7.14; plan docs/plans/phase-7.14.md, section 2.2).

The agent sees the open exception groups of one held load (at most MAX_AGENT_GROUPS, the largest), each with its rule
and pattern, and sample rows only when SEND_SAMPLE_VALUES is true. It answers one fix per group: a transformation
(steps from the fixed safe set, for one source field), a waiver, a referral to the provider, or none.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from core.domain.common import NonBlank
from core.domain.transformation import MAX_STEPS, TransformStep

FixAction = Literal["transformation", "waive", "refer", "none"]
MAX_AGENT_GROUPS = 20
MAX_SAMPLES = 5


class GroupToFix(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group_key: str
    rule_key: str
    description: str | None
    sql: str | None  # the rule's current SQL (None for duplicate_key)
    elements: list[str]
    pattern: str
    exceptions: int
    source_fields: dict[str, str]  # element key -> the feed's mapped source field
    transformations: dict[str, list[dict[str, Any]]] = {}  # source field -> its current steps
    samples: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_SAMPLES)  # only if allowed
    rejected_before: list[FixAction] = []  # fixes a person rejected for this group (a system rejection is not one)


class ExceptionFixInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    feed_id: UUID
    feed_code: str
    load_id: UUID
    groups: list[GroupToFix] = Field(max_length=MAX_AGENT_GROUPS)


class GroupFix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_key: NonBlank
    action: FixAction
    reason: NonBlank
    confidence: float = Field(ge=0, le=1)
    source_field: str | None = None  # a transformation's column
    steps: list[TransformStep] = Field(default_factory=list, max_length=MAX_STEPS)  # a transformation's full steps


class ExceptionFixOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixes: list[GroupFix]
