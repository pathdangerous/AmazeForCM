"""GoLive: the data owner's approval of a feed's configuration for loading (step 11, phase 6).

One row per approval, versioned per feed, append-only. It pins the mapping ids and rule ids the owner saw and
the test load that proved them (migration 0018, guard G6). The engine loads a feed only while its current
configuration is the one the latest go-live pinned (phase 6 plan, assumption 15).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from core.domain.common import NonBlank


class MappedFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_field: str
    element_key: str
    element_version: int
    data_type: str


class RejectedGlossaryFact(BaseModel):
    """A glossary proposal for a column of this feed that the glossary owner rejected, with the note."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_field: str
    note: str | None = None


class RuleFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_key: str
    version: int
    description: str
    flagged_rows: int | None = None  # the back-test figures of the proposal the rule came from
    total_rows: int | None = None


class StageFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str
    outcome: str
    rows_in: int
    rows_out: int


class TestLoadFacts(BaseModel):
    __test__ = False  # not a pytest class

    model_config = ConfigDict(frozen=True, extra="forbid")

    load_id: UUID
    rows: int
    stages: list[StageFact]
    flags: dict[str, int]  # rows flagged per rule key in the test load


class PendingCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    glossary: int
    mapping: int
    rule: int


class ReviewSummaryInput(BaseModel):
    """What the ReviewSummariserAgent is told (phase 6 plan, assumption 23): facts only, no glossary. The ids are
    the configuration the summary is about; the owner's approval pins exactly these."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feed_id: UUID
    feed_code: str
    source_name: str
    owner_id: str
    licence: str
    permitted_use: str
    redistribution: str
    visibility: str
    mapped: list[MappedFact]
    unmapped_columns: list[str]
    rejected_glossary: list[RejectedGlossaryFact]
    rules: list[RuleFact]
    test_load: TestLoadFacts
    pending: PendingCounts
    mapping_ids: list[UUID]
    rule_ids: list[UUID]
    test_load_id: UUID


class ReviewSummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: NonBlank
    highlights: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ReviewSummaryPayload(BaseModel):
    """The payload of a `review_summary` proposal: the draft, the facts code filled, and the pins that
    trg_feed_go_lives_b_guard compares the go-live with (mapping_ids, rule_ids, test_load_id)."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    highlights: list[str]
    risks: list[str]
    unmapped_columns: list[str]
    rejected_glossary: list[RejectedGlossaryFact]
    mapping_ids: list[UUID]
    rule_ids: list[UUID]
    test_load_id: UUID


class GoLive(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    version: int
    proposal_id: UUID
    approved_by: str
    mapping_ids: list[UUID]
    rule_ids: list[UUID]
    test_load_id: UUID
    hypercare_until: date
