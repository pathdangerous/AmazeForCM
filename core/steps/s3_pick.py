"""S3 pick: the requester picks a listed source and accepts its conditions (phase 3 plan, assumptions 33 to
37 and 56; option A).

Outcomes (CLAUDE.md): source_picked; approval_missing.

- The pick carries accepted_conditions: the conditions the requester was shown. They must equal the
  catalogue's conditions for that source now. Missing: approval_missing (not_accepted); different:
  approval_missing (conditions_changed), with the conditions in force to accept. Nothing is written
  then: no approval, no feed, no choice, no fetch; the status stays awaiting_source_choice.
- Accepted: the requester's approval of the source_list proposal (the only approval of a source), the
  source's candidate feed (one per source), the next source_choices version with the accepted
  conditions, and status source_selected. The first fetch is not run here: the caller runs it in its own
  transaction after committing this one, as for a known source (assumption 13).
- decline_source(): the requester rejects a listed source; if no listed source is left, the requirement
  becomes no_source_found.
Only the requirement's own requester picks or declines. The step flushes; the caller commits.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.proposal import Proposal
from core.domain.sourcing import SourceSummary
from core.repositories.errors import ProposalAlreadyDecided, ProposalForAnotherRequirement, RequirementNotInStatus
from core.repositories.proposals import ProposalRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.steps.errors import NotTheRequester
from core.steps.known_source import feed_for
from core.steps.result import StepResult
from core.steps.s2_sources import listed_sources
from ports.connectors.catalogue import SourceCatalogue, SourceConditions
from ports.identity.base import User


class SourcePicked(StepResult):
    outcome: Literal["source_picked"] = "source_picked"
    source: SourceSummary
    feed_code: str


class ApprovalMissing(StepResult):
    outcome: Literal["approval_missing"] = "approval_missing"
    source: SourceSummary
    conditions: SourceConditions  # the conditions in force: accept these to continue
    reason: Literal["not_accepted", "conditions_changed"]  # changed: what was accepted is not what is in force


PickResult = Annotated[SourcePicked | ApprovalMissing, Field(discriminator="outcome")]


class Declined(BaseModel):
    """Not an outcome: CLAUDE.md names none for a declined source."""

    model_config = ConfigDict(frozen=True)

    source_code: str
    status: Literal["awaiting_source_choice", "no_source_found"]
    remaining: int


def _listed_proposal(session: Session, *, requirement_id: UUID, user: User, proposal_id: UUID) -> Proposal:
    requirement = RequirementRepository(session).get(requirement_id)
    if user.id != requirement.requester_id:
        raise NotTheRequester(f"Only {requirement.requester_id} picks the source of requirement {requirement_id}")
    if requirement.status != "awaiting_source_choice":
        raise RequirementNotInStatus(
            f"Requirement {requirement_id} is {requirement.status}; a source is picked from awaiting_source_choice")
    proposal = ProposalRepository(session).get(proposal_id)
    if proposal.kind != "source_list" or proposal.requirement_id != requirement_id:
        raise ProposalForAnotherRequirement(f"Proposal {proposal_id} is not a listed source of {requirement_id}")
    if proposal.status != "pending":
        raise ProposalAlreadyDecided(f"Proposal {proposal_id} is already {proposal.status}")
    return proposal


def pick_source(session: Session, *, requirement_id: UUID, user: User, proposal_id: UUID,
                accepted_conditions: SourceConditions | None, catalogue: SourceCatalogue) -> SourcePicked | ApprovalMissing:
    proposal = _listed_proposal(session, requirement_id=requirement_id, user=user, proposal_id=proposal_id)
    entry = catalogue.get(proposal.payload["source_code"])
    source = SourceSummary.of(entry)
    if accepted_conditions is None:
        return ApprovalMissing(source=source, conditions=entry.conditions, reason="not_accepted")
    if accepted_conditions != entry.conditions:
        return ApprovalMissing(source=source, conditions=entry.conditions, reason="conditions_changed")

    ProposalRepository(session).record_decision(proposal.id, user=user, decision="approved")
    feed = feed_for(session, entry=entry, requirement_id=requirement_id)
    SourceChoiceRepository(session).record_pick(
        requirement_id=requirement_id, feed_id=feed.id, source_code=entry.code, proposal_id=proposal.id,
        accepted_conditions=entry.conditions, decided_by=user.id,
    )
    RequirementRepository(session).advance(requirement_id, "source_selected")
    return SourcePicked(source=source, feed_code=feed.code)


def decline_source(session: Session, *, requirement_id: UUID, user: User, proposal_id: UUID, note: str | None,
                   catalogue: SourceCatalogue) -> Declined:
    proposal = _listed_proposal(session, requirement_id=requirement_id, user=user, proposal_id=proposal_id)
    ProposalRepository(session).record_decision(proposal.id, user=user, decision="rejected",
                                                note=(note or "").strip() or None)
    remaining = len(listed_sources(session, requirement_id, catalogue))
    status: Literal["awaiting_source_choice", "no_source_found"] = "awaiting_source_choice"
    if remaining == 0:
        RequirementRepository(session).advance(requirement_id, "no_source_found")
        status = "no_source_found"
    return Declined(source_code=proposal.payload["source_code"], status=status, remaining=remaining)
