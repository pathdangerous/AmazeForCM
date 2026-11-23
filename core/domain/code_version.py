"""A feed's generated code at one go-live approval (phase 7.12, T3; migration 0030). Append-only."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CodeVersion(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    version: int
    sha256: str
    files: dict[str, str]
    mapping_ids: list[UUID]
    rule_ids: list[UUID]
    transformation_ids: list[UUID]
    test_load_id: UUID | None
    test_load_result: dict[str, Any]
    approved_by: str
