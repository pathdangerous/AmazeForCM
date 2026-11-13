"""Feed: a data delivery Amaze onboards once (steps 01-11) and then loads (12-16).

A feed starts as a candidate (found by the custom data product or named by the backend team) and is
registered at step 04, which needs an owner (the no_owner outcome otherwise). Licence and visibility
fields arrive with phase 4.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from core.domain.common import NonBlank, SnakeName

FeedStage = Literal["candidate", "registered", "rejected"]
FEED_STAGES: tuple[str, ...] = get_args(FeedStage)


class FeedDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: SnakeName
    name: NonBlank
    requirement_id: UUID | None = None
    source_ref: NonBlank | None = None
    owner_id: NonBlank | None = None


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
