"""Release a selected source (phase 3 plan, assumptions 49 to 55).

A source_selected requirement's source is released by its requester (requester_asked) or by the fetch's
rule after N failed days in a row (source_down_n_days, in s4_fetch). The release is the next
source_choices version; the feed and its fetches stay. The requirement moves to awaiting_source_choice
if a listed source can still be picked, otherwise to no_source_found (always so for a known source).
The backend team is told once per release. CLAUDE.md names no outcome for a release, so the result is a
plain model, not a StepResult.

Rules execute: no model call. The step flushes; the caller commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.domain.sourcing import ReleaseReason
from core.repositories.errors import RequirementNotInStatus
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.steps.errors import NotTheRequester
from core.steps.s2_sources import listed_sources
from ports.connectors.catalogue import SourceCatalogue, UnknownSource
from ports.identity.base import User
from ports.notify.base import Notifier


class Released(BaseModel):
    model_config = ConfigDict(frozen=True)

    requirement_id: UUID
    source_code: str
    source_name: str
    reason: ReleaseReason
    version: int  # the released row's version
    status: Literal["awaiting_source_choice", "no_source_found"]


def release_source(session: Session, *, requirement_id: UUID, reason: ReleaseReason, user: User | None,
                   catalogue: SourceCatalogue, notifier: Notifier, backend_team: Sequence[str]) -> Released:
    """user: the requester for requester_asked; None for a release by rule (the N-day rule, or step 03's
    rejection, candidate_rejected). Only candidate_rejected releases an onboarding requirement: its file
    was handed to step 02, and step 02 rejected it (phase 4 plan, assumption 24)."""
    if (reason == "requester_asked") != (user is not None):
        raise ValueError("a requester_asked release names the requester; a release by rule names nobody")
    requirements = RequirementRepository(session)
    requirement = requirements.lock(requirement_id)
    if user is not None and user.id != requirement.requester_id:
        raise NotTheRequester(f"Only {requirement.requester_id} releases the source of requirement {requirement_id}")
    allowed = ("source_selected", "onboarding") if reason == "candidate_rejected" else ("source_selected",)
    if requirement.status not in allowed:
        raise RequirementNotInStatus(
            f"Requirement {requirement_id} is {requirement.status}; a {reason} release is from {', '.join(allowed)}")

    choice = SourceChoiceRepository(session).record_release(requirement_id=requirement_id, reason=reason,
                                                            decided_by=user.id if user else None)
    status: Literal["awaiting_source_choice", "no_source_found"] = (
        "awaiting_source_choice" if listed_sources(session, requirement_id, catalogue) else "no_source_found")
    requirements.advance(requirement_id, status)
    try:
        name = catalogue.get(choice.source_code).name
    except UnknownSource:  # the catalogue changed since the selection
        name = choice.source_code
    for user_id in backend_team:
        notifier.notify(recipient=user_id, subject=f"Source released: {choice.source_code}",
                        body=f"Requirement {requirement_id} ({requirement.title}) released source "
                             f"{choice.source_code} ({reason}). It is now {status}.")
    return Released(requirement_id=requirement_id, source_code=choice.source_code, source_name=name, reason=reason,
                    version=choice.version, status=status)
