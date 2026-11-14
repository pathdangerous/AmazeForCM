"""S1 scope: public or private, who provides, where to search (phase 3 plan, assumptions 27 to 29).

Outcomes (CLAUDE.md): scoped; scope_not_confirmed.

- propose_scope(): the IntentAgent proposes one scope (a scope proposal for the requester) and the
  requirement waits as awaiting_scope_confirmation. From defined (after no_known_source), or, with
  again=True, from scope_not_confirmed or no_source_found with the requester's note. The status is
  checked before the agent is called, so a refused request makes no model call.
- decide_scope(): only the requirement's own requester approves or rejects; the requester never edits
  the scope. scoped writes the confirmed scope (requirement_scopes) and moves to scope_confirmed;
  scope_not_confirmed writes the rejection (with its note) and moves to scope_not_confirmed.

The agent is an argument (assumption 22): this module never imports agents. It flushes; the caller commits.
"""

from __future__ import annotations

from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.proposal import Decision
from core.domain.sourcing import AgentRun, ChannelRef, ElementRef, IntentInput, RequirementScope
from core.repositories.errors import RequirementNotInStatus
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirement_scopes import RequirementScopeRepository
from core.repositories.requirements import RequirementRepository
from core.steps.errors import NothingToDecide, NotTheRequester
from core.steps.result import StepResult
from ports.connectors.catalogue import SourceCatalogue
from ports.identity.base import User

FIRST_FROM = ("defined",)
AGAIN_FROM = ("scope_not_confirmed", "no_source_found")


class RunsAgent(Protocol):
    def run(self, session: Session, agent_input: BaseModel) -> AgentRun: ...


class Scoped(StepResult):
    outcome: Literal["scoped"] = "scoped"
    scope: RequirementScope


class ScopeNotConfirmed(StepResult):
    outcome: Literal["scope_not_confirmed"] = "scope_not_confirmed"
    note: str | None


ScopeResult = Annotated[Scoped | ScopeNotConfirmed, Field(discriminator="outcome")]


class ScopeProposed(BaseModel):
    """What the requester is asked to confirm. Not an outcome: S1 has not decided anything yet."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    log_id: UUID
    visibility: str
    provider: str
    where_to_search: list[str]
    reason: str


def propose_scope(session: Session, *, requirement_id: UUID, agent: RunsAgent, catalogue: SourceCatalogue,
                  note: str | None = None, again: bool = False) -> ScopeProposed:
    requirements = RequirementRepository(session)
    requirement = requirements.get(requirement_id)
    allowed = AGAIN_FROM if again else FIRST_FROM
    if requirement.status not in allowed:
        raise RequirementNotInStatus(
            f"Requirement {requirement_id} is {requirement.status}; a scope is proposed from {', '.join(allowed)}")
    glossary = GlossaryRepository(session)
    agent_input = IntentInput(
        requirement_id=requirement.id, requirement_text=requirement.title, purpose=requirement.description or None,
        note=(note or "").strip() or None,
        elements=[ElementRef(key=k, name=glossary.get_current(k).name) for k in requirement.elements],
        terms=requirement.terms,
        channels=[ChannelRef(channel=c, visibility=v) for c, v in catalogue.channels()],
    )
    run = agent.run(session, agent_input)
    if len(run.proposals) != 1 or run.proposals[0].kind != "scope":
        raise ValueError("S1 expects exactly one scope proposal from the agent")
    proposal = run.proposals[0]
    requirements.advance(requirement.id, "awaiting_scope_confirmation")
    payload = proposal.payload
    return ScopeProposed(proposal_id=proposal.id, log_id=run.log_id, visibility=payload["visibility"],
                         provider=payload["provider"], where_to_search=payload["where_to_search"],
                         reason=payload["reason"])


def pending_scope(session: Session, requirement_id: UUID):
    """The scope proposal waiting for the requester, if any (the newest pending one)."""
    pending = ProposalRepository(session).list_for_requirement(requirement_id, "scope", status="pending")
    return pending[-1] if pending else None


def decide_scope(session: Session, *, requirement_id: UUID, user: User, decision: Decision,
                 note: str | None = None) -> Scoped | ScopeNotConfirmed:
    requirements = RequirementRepository(session)
    requirement = requirements.get(requirement_id)
    if user.id != requirement.requester_id:
        raise NotTheRequester(f"Only {requirement.requester_id} decides the scope of requirement {requirement_id}")
    if requirement.status != "awaiting_scope_confirmation":
        raise RequirementNotInStatus(
            f"Requirement {requirement_id} is {requirement.status}; no scope is waiting to be confirmed")
    proposal = pending_scope(session, requirement_id)
    if proposal is None:
        raise NothingToDecide(f"Requirement {requirement_id} has no scope proposal waiting")
    note = (note or "").strip() or None
    ProposalRepository(session).record_decision(proposal.id, user=user, decision=decision, note=note)
    if decision == "rejected":
        requirements.advance(requirement_id, "scope_not_confirmed")
        return ScopeNotConfirmed(note=note)
    payload = proposal.payload
    scope = RequirementScopeRepository(session).add_version(
        requirement_id=requirement_id, proposal_id=proposal.id, confirmed_by=user.id,
        visibility=payload["visibility"], provider=payload["provider"], where_to_search=payload["where_to_search"],
    )
    requirements.advance(requirement_id, "scope_confirmed")
    return Scoped(scope=scope)
