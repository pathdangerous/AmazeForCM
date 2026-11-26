"""Transformations and key policies (phase 7.8 plan, assumptions 27 to 29).

A transformation is an ordered list of steps for one mapped column, taken only from a fixed safe set. The engine
turns each step into a fixed SQL template (task 4.2); nothing a model writes is ever run. The database repeats
every rule here in a CHECK on the JSON (migration 0023, amaze_valid_transform_steps()).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from core.domain.common import SNAKE, NonBlank

DATE_FORMATS = ("DD-MM-YYYY", "MM-DD-YYYY", "DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD", "YYYYMMDD", "DD.MM.YYYY")
CAST_TYPES = ("string", "integer", "decimal", "date", "datetime", "boolean")
TRANSFORM_OPS = ("trim", "placeholder_to_null", "parse_date", "cast", "rename")
KEY_POLICIES = ("hold", "keep_last", "flag")
MAX_STEPS = 6
MAX_KEY_COLUMNS = 5

DateFormat = Literal["DD-MM-YYYY", "MM-DD-YYYY", "DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD", "YYYYMMDD", "DD.MM.YYYY"]
CastType = Literal["string", "integer", "decimal", "date", "datetime", "boolean"]
KeyPolicyName = Literal["hold", "keep_last", "flag"]


class _Step(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Trim(_Step):
    op: Literal["trim"]


class PlaceholderToNull(_Step):
    op: Literal["placeholder_to_null"]
    values: list[Annotated[str, StringConstraints(max_length=16)]] = Field(min_length=1, max_length=10)


class ParseDate(_Step):
    op: Literal["parse_date"]
    format: DateFormat


class Cast(_Step):
    op: Literal["cast"]
    type: CastType


class Rename(_Step):
    op: Literal["rename"]
    to: Annotated[str, StringConstraints(pattern=SNAKE, max_length=63)]


TransformStep = Annotated[Trim | PlaceholderToNull | ParseDate | Cast | Rename, Field(discriminator="op")]


def _ordered(steps: list) -> list:
    ops = [s.op for s in steps]
    if len(set(ops)) != len(ops):
        raise ValueError("each op may appear at most once")
    if "rename" in ops and ops[-1] != "rename":
        raise ValueError("rename, if present, is the last step")
    return steps


TransformSteps = Annotated[list[TransformStep], Field(min_length=1, max_length=MAX_STEPS), AfterValidator(_ordered)]


class TransformationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feed_id: UUID
    source_field: NonBlank
    steps: TransformSteps

    def steps_json(self) -> list[dict]:
        return [s.model_dump(mode="json") for s in self.steps]


class Transformation(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    version: int
    feed_id: UUID
    source_field: str
    steps: TransformSteps
    proposal_id: UUID
    created_by: str
    created_at: datetime


# ---------------------------------------------------------------- the TransformationAgent (task 4.3)


class ColumnToTransform(BaseModel):
    """One mapped column of a registered feed, with the facts code found in its file. No values (see
    TransformationInput.samples): names, types and counts only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_field: str
    element_key: str
    element_type: str  # the mapped element's glossary data type
    duckdb_type: str  # what DuckDB detected in the file
    null_count: int
    row_count: int
    placeholder_values: list[str]
    placeholder_count: int
    text_date_format: str | None
    date_format_ambiguous: bool
    all_empty: bool
    constant: bool


class RejectedTransform(BaseModel):
    """Phase 7.22, T5: the ops of a transformation proposal a person rejected for a column, and why. The agent is not to
    propose any of these ops for that column again."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_field: str
    ops: list[str]
    reason: str


class TransformationInput(BaseModel):
    """What the TransformationAgent sees: the feed's currently mapped columns (never the glossary), and, only when
    SEND_SAMPLE_VALUES is true (decision D3), at most 5 values per column."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feed_id: UUID
    feed_code: str
    columns: list[ColumnToTransform]
    samples: dict[str, list[str]] = {}
    profile_actions: list[dict[str, Any]] = Field(default_factory=list)  # T1: selected profile actions
    # True when the profile step proposed actions: then clean a column only if an action for it was selected
    profile_actions_decided: bool = False
    rejected: list[RejectedTransform] = Field(default_factory=list)  # T5: a person rejected these ops, with a reason


class ColumnTransforms(BaseModel):
    """The steps proposed for one column; none means there is nothing to do. The steps are checked as a whole
    (order, each op once) when a proposal is made from them."""

    model_config = ConfigDict(extra="forbid")

    source_field: NonBlank
    steps: list[TransformStep] = Field(max_length=MAX_STEPS)
    reason: NonBlank
    confidence: float = Field(ge=0, le=1)


class TransformationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[ColumnTransforms]


class TransformationProposalPayload(BaseModel):
    """A transformation proposal: one column of one feed and its steps (trg_transformations_b_matches_proposal
    reads source_field and steps)."""

    model_config = ConfigDict(extra="forbid")

    source_field: NonBlank
    element_key: NonBlank
    steps: TransformSteps
    reason: NonBlank
    confidence: float = Field(ge=0, le=1)


def _distinct(columns: list[str]) -> list[str]:
    if len(set(columns)) != len(columns):
        raise ValueError("a key column appears once")
    return columns


KeyColumns = Annotated[list[NonBlank], Field(min_length=1, max_length=MAX_KEY_COLUMNS), AfterValidator(_distinct)]


class KeyPolicyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: KeyPolicyName
    key_columns: KeyColumns
    reason: NonBlank


class KeyPolicy(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    feed_id: UUID
    version: int
    policy: KeyPolicyName
    key_columns: list[str]
    set_by: str
    set_at: datetime
    reason: str
