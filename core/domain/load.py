"""Load: one delivery (file) of a registered feed, tracked through steps 12 to 15.

Statuses are the names in CLAUDE.md. Phase 7 adds more by migration (for example quarantined, for the
resent_quarantined outcome). Per-stage tracking (step 13) arrives with phase 6.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from core.domain.common import NonBlank

LoadStatus = Literal["received", "held", "failed_rolled_back", "released"]
LOAD_STATUSES: tuple[str, ...] = get_args(LoadStatus)


class LoadDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_ref: NonBlank  # BlobStore reference of the delivered file
    received_at: datetime | None = None  # defaults to the moment of insert


class Load(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    file_ref: str
    received_at: datetime
    status: LoadStatus
