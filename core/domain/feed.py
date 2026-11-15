"""Feed: a data delivery Amaze onboards once (steps 01-11) and then loads (12-16).

A feed starts as a candidate (found by the custom data product or named by the backend team) and is
registered at step 04, which needs an owner (the no_owner outcome otherwise). Its licence and visibility
are a FeedRegistration (phase 4, feed_registrations); a rejected feed was rejected at step 03.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from core.domain.common import NonBlank, SnakeName

FeedStage = Literal["candidate", "registered", "rejected"]
FEED_STAGES: tuple[str, ...] = get_args(FeedStage)

# How a feed's source is read (phase 3): one of the source connectors in ports/connectors.
Connector = Literal["local_file", "http"]
CONNECTORS: tuple[str, ...] = get_args(Connector)


class FeedDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: SnakeName
    name: NonBlank
    requirement_id: UUID | None = None
    source_ref: NonBlank | None = None
    owner_id: NonBlank | None = None
    source_code: SnakeName | None = None  # the catalogue source this feed reads; one feed per source
    connector: Connector | None = None

    @model_validator(mode="after")
    def _source_complete(self) -> FeedDraft:
        if (self.source_code is None) != (self.connector is None):
            raise ValueError("source_code and connector are set together")
        if self.connector is not None and self.source_ref is None:
            raise ValueError("a feed with a connector needs a source_ref")
        return self


FeedVisibility = Literal["open", "restricted"]  # the same two words as a product's (phase 4 plan, assumption 31)


class FeedRegistrationDraft(BaseModel):
    """Step 04: what is registered. The licence fields are copied from the catalogue entry by the step."""

    model_config = ConfigDict(extra="forbid")

    feed_id: UUID
    signoff_id: UUID
    owner_id: NonBlank
    licence: NonBlank
    permitted_use: NonBlank
    redistribution: NonBlank
    visibility: FeedVisibility
    registered_by: NonBlank


class FeedRegistration(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    version: int
    signoff_id: UUID
    owner_id: str
    licence: str
    permitted_use: str
    redistribution: str
    visibility: FeedVisibility
    registered_by: str


class Feed(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    code: str
    name: str
    stage: FeedStage
    requirement_id: UUID | None
    owner_id: str | None
    source_ref: str | None
    registered_at: datetime | None
    source_code: str | None
    connector: Connector | None
