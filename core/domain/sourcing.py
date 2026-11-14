"""The custom data product (Known source?, S1-S4): which source a requirement was handed to, and
every fetch of a feed's source.

Phase 3a: SourceChoice (one per requirement, basis known_source) and SourceFetch (fetched or
source_down). Phase 3b adds the requester's pick, the scope and the agents' models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.domain.common import NonBlank
from ports.connectors.catalogue import SourceConditions, SourceEntry


class SourceSummary(BaseModel):
    """A catalogue source as shown to the requester: who provides it and its conditions of use."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    provider: str
    visibility: str
    conditions: SourceConditions

    @classmethod
    def of(cls, entry: SourceEntry) -> SourceSummary:
        return cls(code=entry.code, name=entry.name, provider=entry.provider, visibility=entry.visibility,
                   conditions=entry.conditions)

ChoiceBasis = Literal["known_source"]
CHOICE_BASES: tuple[str, ...] = get_args(ChoiceBasis)

FetchOutcome = Literal["fetched", "source_down"]
FETCH_OUTCOMES: tuple[str, ...] = get_args(FetchOutcome)


class SourceChoice(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    requirement_id: UUID
    feed_id: UUID
    source_code: str
    basis: ChoiceBasis


class SourceFetchDraft(BaseModel):
    """One fetch, validated before it is written: a fetched file has a blob and a size above zero; a
    source_down has a reason and no file."""

    model_config = ConfigDict(extra="forbid")

    outcome: FetchOutcome
    blob_ref: NonBlank | None = None
    size_bytes: int | None = Field(default=None, gt=0)
    detail: NonBlank | None = None

    @model_validator(mode="after")
    def _consistent(self) -> SourceFetchDraft:
        if self.outcome == "fetched" and (self.blob_ref is None or self.size_bytes is None or self.detail is not None):
            raise ValueError("a fetched row has a blob_ref and a size_bytes, and no detail")
        if self.outcome == "source_down" and (self.detail is None or self.blob_ref is not None
                                              or self.size_bytes is not None):
            raise ValueError("a source_down row has a detail, and no blob_ref or size_bytes")
        return self


class SourceFetch(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    outcome: FetchOutcome
    blob_ref: str | None
    size_bytes: int | None
    detail: str | None
