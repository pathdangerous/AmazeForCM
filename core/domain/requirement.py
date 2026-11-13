"""Requirement: what a requester asked for when no product or source had the data (step 01).

Statuses are the names in CLAUDE.md. Phase 3 will add more by migration (for example
awaiting_source_choice).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from core.domain.common import NonBlank

RequirementStatus = Literal["defined", "onboarding", "ready", "access_restricted", "rejected"]
REQUIREMENT_STATUSES: tuple[str, ...] = get_args(RequirementStatus)


class RequirementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requester_id: NonBlank
    title: NonBlank
    description: str = ""


class Requirement(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    requester_id: str
    title: str
    description: str
    status: RequirementStatus
