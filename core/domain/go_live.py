"""GoLive: the data owner's approval of a feed's configuration for loading (step 11, phase 6).

One row per approval, versioned per feed, append-only. It pins the mapping ids and rule ids the owner saw and
the test load that proved them (migration 0018, guard G6). The engine loads a feed only while its current
configuration is the one the latest go-live pinned (phase 6 plan, assumption 15).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
