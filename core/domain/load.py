"""Load: one delivery (file) of a registered feed, tracked through steps 12 to 15.

Statuses are the names in CLAUDE.md, plus `loaded` (phase 6, migration 0018): the engine (step 13) ended with
all four stages completed. Phase 7 adds more by migration (for example quarantined, for the
resent_quarantined outcome). A load has a purpose: `go_live_test` (step 11's test load, before the owner
approves) or `scheduled` (the daily job, after a go-live; it names that go-live). Each stage of the engine is
a LoadStage row; each file row that reached the standard model is a StandardRecord.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.domain.common import NonBlank

LoadStatus = Literal["received", "loaded", "held", "failed_rolled_back", "released"]
LOAD_STATUSES: tuple[str, ...] = get_args(LoadStatus)

LoadPurpose = Literal["go_live_test", "scheduled"]
LOAD_PURPOSES: tuple[str, ...] = get_args(LoadPurpose)

Stage = Literal["raw", "curated", "validated", "standard"]
STAGES: tuple[str, ...] = get_args(Stage)  # in the order the engine runs them

StageOutcome = Literal["stage_completed", "failed_rolled_back"]
STAGE_OUTCOMES: tuple[str, ...] = get_args(StageOutcome)


class LoadDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_ref: NonBlank  # BlobStore reference of the delivered file
    purpose: LoadPurpose
    fetch_id: UUID | None = None  # the source_fetches row the file came from
    go_live_id: UUID | None = None  # a scheduled load names the go-live that approved it; a test load has none
    received_at: datetime | None = None  # defaults to the moment of insert

    @model_validator(mode="after")
    def _purpose_fits_the_go_live(self) -> LoadDraft:
        if (self.purpose == "scheduled") != (self.go_live_id is not None):
            raise ValueError("a scheduled load names its go-live; a go_live_test load has none")
        return self


class Load(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    file_ref: str
    received_at: datetime
    status: LoadStatus
    purpose: LoadPurpose
    fetch_id: UUID | None
    go_live_id: UUID | None
    finished_at: datetime | None
    failed_stage: Stage | None
    failure: str | None


class LoadStageDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Stage
    outcome: StageOutcome
    rows_in: int = Field(ge=0)
    rows_out: int = Field(ge=0)
    detail: dict[str, Any]
    started_at: datetime
    finished_at: datetime


class LoadStage(LoadStageDraft):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    load_id: UUID


class StandardRecordDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_number: int = Field(ge=1)  # the file's data row, from 1
    values: dict[str, Any]  # element key -> value (decimals as exact strings, dates ISO)
    flags: list[str] = Field(default_factory=list)  # the keys of the rules that flagged the row


class StandardRecord(StandardRecordDraft):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    load_id: UUID
    feed_id: UUID
