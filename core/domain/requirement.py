"""Requirement: what a requester asked for when no product or source had the data (step 01).

A requirement lists the glossary elements it asks for, and the terms the requester ticked that are not
in the glossary yet (the element_not_in_glossary outcome). Both lists are written once, with the
requirement.

Statuses are the names in CLAUDE.md, plus the custom data product's (phase 3, by migration).
A status changes only along REQUIREMENT_TRANSITIONS.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.domain.common import NonBlank, SnakeName

RequirementStatus = Literal["defined", "onboarding", "ready", "access_restricted", "rejected", "source_selected"]
REQUIREMENT_STATUSES: tuple[str, ...] = get_args(RequirementStatus)

# Every allowed status change (from, to), and nothing else (phase 3 plan, assumption 4 and section 5.1).
# RequirementRepository.advance() refuses any other move with RequirementNotInStatus.
REQUIREMENT_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("defined", "source_selected"),  # Known source? -> known_source: the source is selected
    ("source_selected", "onboarding"),  # S4 -> fetched: the file is handed to step 02
})


class RequirementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requester_id: NonBlank
    title: NonBlank
    description: str = ""
    element_keys: list[SnakeName] = Field(default_factory=list)
    terms: list[NonBlank] = Field(default_factory=list)

    @model_validator(mode="after")
    def _each_once(self) -> RequirementDraft:
        if len(set(self.element_keys)) != len(self.element_keys):
            raise ValueError("an element can appear only once")
        if len(set(self.terms)) != len(self.terms):
            raise ValueError("a term can appear only once")
        return self


class Requirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    created_at: datetime
    requester_id: str
    title: str
    description: str
    status: RequirementStatus
    elements: list[str]  # glossary element keys, in alphabetical order
    terms: list[str]  # ticked terms that are not in the glossary, in alphabetical order
