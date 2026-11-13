"""Versioned configuration of a feed: Mapping (level 1) and Rule.

- Mapping, level 1: one source field to one glossary element, with no transformation.
- Rule: one SQL validation rule (step 09), back-tested and approved by the steward, then the checker.

Both are append-only. The current configuration is the highest version per source field or rule key.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from core.domain.common import NonBlank, SnakeName


class MappingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_field: NonBlank  # as it appears in the source file; any case
    element_key: SnakeName


class Mapping(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    source_field: str
    element_key: str
    version: int
    proposal_id: UUID


class RuleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_key: SnakeName
    sql: NonBlank
    description: NonBlank


class Rule(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    rule_key: str
    version: int
    sql: str
    description: str
    proposal_id: UUID
