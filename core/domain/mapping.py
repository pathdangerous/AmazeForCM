"""Steps 05 and 06 (phase 5 plan): candidate elements, new columns and the glossary builder's options.

A Candidate is one glossary element offered for one column or term (core/steps/candidates.py builds the
lists; no agent is ever sent the whole glossary). A NewColumn is a column of a registered feed's latest
profile that step 05 looks at. An ElementOption is one thing the glossary owner may approve.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.domain.common import NonBlank, SnakeName
from core.domain.glossary import DataType, GlossaryElementDraft

GlossaryRunReason = Literal["registered", "requested", "sent_back"]
GLOSSARY_RUN_REASONS: tuple[str, ...] = get_args(GlossaryRunReason)
GlossaryRunStatus = Literal["queued", "running", "done", "failed"]
GLOSSARY_RUN_STATUSES: tuple[str, ...] = get_args(GlossaryRunStatus)
GlossaryOutcome = Literal["proposed", "duplicate_alias", "conflict_to_owner"]
GLOSSARY_OUTCOMES: tuple[str, ...] = get_args(GlossaryOutcome)
MAX_ATTEMPTS = 3


class Candidate(BaseModel):
    """One glossary element offered for a column or a term, with how well its name matched."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name: str
    data_type: DataType
    definition: str
    aliases: list[str]
    score: float
    from_hint: bool
    type_fits: bool


class NewColumn(BaseModel):
    """A column step 05 looks at. type is DuckDB's, None when unknown; declared_as is the spec's other-field
    name when the source declares the column as one; sent_back when step 06 found no element for it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str | None
    null_rate: float | None = None
    distinct_count: int | None = None
    declared_as: str | None = None
    sent_back: bool = False
    candidates: list[Candidate] = Field(default_factory=list, max_length=10)


class ColumnToMap(BaseModel):
    """A column step 06 asks the MappingAgent about: its statistics, at most 10 candidates (the spec's hint
    among them, marked; the steward's rejected elements left out) and its current element, if mapped."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str | None
    null_rate: float | None = None
    distinct_count: int | None = None
    candidates: list[Candidate] = Field(default_factory=list, max_length=10)
    current: str | None = None


# ---------------------------------------------------------------- agent inputs and outputs


class GlossaryBuilderInput(BaseModel):
    """What the GlossaryBuilderAgent sees: one feed's new columns, each with its candidates; never the
    whole glossary (assumption 13). No values: names, types and counts only."""

    model_config = ConfigDict(frozen=True)

    run_id: UUID
    feed_id: UUID
    source_code: str
    source_name: str
    source_domain: str  # the domain most of the source's declared elements belong to
    domains: list[str]
    columns: list[NewColumn]


class GlossaryAnswer(BaseModel):
    """Per column: a candidate it is the same as, a new element, or neither (leave it to step 06)."""

    model_config = ConfigDict(extra="forbid")

    column: str
    same_as: SnakeName | None = None
    new_element: GlossaryElementDraft | None = None
    reason: NonBlank

    @model_validator(mode="after")
    def _one(self) -> GlossaryAnswer:
        if self.same_as is not None and self.new_element is not None:
            raise ValueError("an answer is same_as or a new element, not both")
        return self


class GlossaryBuilderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[GlossaryAnswer]


class MappingInput(BaseModel):
    """What the MappingAgent (and the MappingEvaluator) sees. feed_id is None for a gold source."""

    model_config = ConfigDict(frozen=True)

    feed_id: UUID | None
    source_code: str
    source_name: str
    source_description: str | None
    columns: list[ColumnToMap]


class MappingAnswer(BaseModel):
    """Per column: zero, one or several element keys, best first, and why."""

    model_config = ConfigDict(extra="forbid")

    column: str
    elements: list[str]
    reason: NonBlank


class MappingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[MappingAnswer]


class ElementOption(BaseModel):
    """new_element: version 1 of element.key; alias: the next version of key with alias added."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["new_element", "alias"]
    key: SnakeName
    alias: SnakeName | None = None
    element: GlossaryElementDraft | None = None

    @model_validator(mode="after")
    def _shape(self) -> ElementOption:
        if self.kind == "new_element" and not (self.element is not None and self.alias is None
                                               and self.element.key == self.key):
            raise ValueError("a new element option holds the element, under its own key, and no alias")
        if self.kind == "alias" and not (self.alias is not None and self.element is None):
            raise ValueError("an alias option holds the alias and no element")
        return self


# ---------------------------------------------------------------- proposal payloads (migration 0016 reads them)


class GlossaryProposalPayload(BaseModel):
    """A glossary_element proposal: one column of one feed, the outcome code named, the options, and the
    recommended one (an index; None for a conflict). trg_glossary_elements_b_option reads options."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_field: NonBlank
    outcome: GlossaryOutcome
    options: list[ElementOption]
    recommended: int | None
    reason: str
    run_id: UUID

    @model_validator(mode="after")
    def _recommendation(self) -> GlossaryProposalPayload:
        if self.outcome == "conflict_to_owner":
            if self.recommended is not None:
                raise ValueError("a conflict recommends nothing")
        elif not (len(self.options) == 1 and self.recommended == 0):
            raise ValueError("proposed and duplicate_alias have one option, and recommend it")
        return self


class MappingProposalPayload(BaseModel):
    """A mapping proposal: one column of one feed and its candidates (keys, best first). One candidate is
    recommended; several are a tie (no recommendation). trg_mappings_b_matches_proposal reads candidates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_field: NonBlank
    column_type: str | None
    candidates: list[SnakeName] = Field(min_length=1, max_length=10)
    recommended: SnakeName | None
    reason: str

    @model_validator(mode="after")
    def _recommendation(self) -> MappingProposalPayload:
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("each candidate once")
        expected = self.candidates[0] if len(self.candidates) == 1 else None
        if self.recommended != expected:
            raise ValueError("one candidate is recommended; a tie recommends nothing")
        return self


# ---------------------------------------------------------------- glossary runs (migration 0016)


class GlossaryRun(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    profile_id: UUID
    reason: GlossaryRunReason
    columns: list[str]
    requested_by: str
    status: GlossaryRunStatus
    attempt: int
    lease_until: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    proposal_log_id: UUID | None
    results: dict[str, Any] | None
    error: str | None
