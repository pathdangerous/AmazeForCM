"""S1 scope: public or private, who provides, where to search (phase 3 plan, assumptions 27 to 29).

Outcomes (CLAUDE.md): scoped; scope_not_confirmed.

- scope_input() (read), then the caller ends the transaction, then record_scope(): the IntentAgent
  proposes one scope (a scope proposal for the requester) and the requirement waits as
  awaiting_scope_confirmation. From defined (after no_known_source), or, with
  again=True, from scope_not_confirmed or no_source_found with the requester's note. The status is
  checked before the agent is called, so a refused request makes no model call.
- decide_scope(): only the requirement's own requester approves or rejects; the requester never edits
  the scope. scoped writes the confirmed scope (requirement_scopes) and moves to scope_confirmed;
  scope_not_confirmed writes the rejection (with its note) and moves to scope_not_confirmed.

The agent is an argument (assumption 22): this module never imports agents. It flushes; the caller commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Literal, Protocol
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
from ports.notify.base import Notifier

if TYPE_CHECKING:  # s2_sources imports this module
    from core.steps.s2_sources import NoFit

FIRST_FROM = ("defined",)
AGAIN_FROM = ("scope_not_confirmed", "no_source_found")


class RunsAgent(Protocol):
    def run(self, session: Session, agent_input: BaseModel, *, before_write=None) -> AgentRun: ...


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


def locked_in(session: Session, requirement_id: UUID, allowed: tuple[str, ...], what: str):
    """Lock the requirement and check its status: the check every step makes before it writes."""
    requirement = RequirementRepository(session).lock(requirement_id)
    if requirement.status not in allowed:
        raise RequirementNotInStatus(
            f"Requirement {requirement_id} is {requirement.status}; {what} from {', '.join(allowed)}")
    return requirement


def scope_input(session: Session, *, requirement_id: UUID, catalogue: SourceCatalogue, note: str | None = None,
                again: bool = False) -> IntentInput:
    """Read only: the IntentAgent's input, after checking the status. The caller then ends the read
    transaction, so no connection is held during the model call (review fix 2)."""
    requirement = RequirementRepository(session).get(requirement_id)
    allowed = AGAIN_FROM if again else FIRST_FROM
    if requirement.status not in allowed:
        raise RequirementNotInStatus(
            f"Requirement {requirement_id} is {requirement.status}; a scope is proposed from {', '.join(allowed)}")
    glossary = GlossaryRepository(session)
    return IntentInput(
        requirement_id=requirement.id, requirement_text=requirement.title, purpose=requirement.description or None,
        note=(note or "").strip() or None,
        elements=[ElementRef(key=k, name=glossary.get_current(k).name) for k in requirement.elements],
        terms=requirement.terms,
        channels=[ChannelRef(channel=c, visibility=v) for c, v in catalogue.channels()],
    )


def record_scope(session: Session, *, agent: RunsAgent, agent_input: IntentInput, again: bool = False,
                 notifier: Notifier | None = None, backend_team: Sequence[str] = ()) -> ScopeProposed | NoFit:
    """The model call (its call row committed at once by Agent.run()), then, with the requirement locked
    and its status checked again, the scope proposal and the move to awaiting_scope_confirmation. A
    status change during the call gives RequirementNotInStatus: the call gets a discard row, and nothing
    else is written.

    A scope with no channel to search cannot be confirmed into anything: it is no_fit at once
    (no_source_found, the backend team told), and the requester is not asked (review fix 5)."""
    allowed = AGAIN_FROM if again else FIRST_FROM
    requirement_id = agent_input.requirement_id
    locked = []
    run = agent.run(session, agent_input, before_write=lambda s: locked.append(
        locked_in(s, requirement_id, allowed, "a scope is proposed")))
    if len(run.proposals) != 1 or run.proposals[0].kind != "scope":
        raise ValueError("S1 expects exactly one scope proposal from the agent")
    proposal = run.proposals[0]
    payload = proposal.payload
    if not payload["where_to_search"]:
        from core.steps.s2_sources import NoFit  # s2 imports this module

        requirement = locked[0]
        if requirement.status != "no_source_found":
            RequirementRepository(session).advance(requirement_id, "no_source_found")
        for user_id in backend_team:
            notifier.notify(recipient=user_id, subject=f"No source fits: {requirement.title}",
                            body=f"Requirement {requirement_id} ({requirement.title}): the proposed "
                                 f"{payload['visibility']} scope names no channel to search (no_channel_in_scope).")
        return NoFit(reason="no_channel_in_scope")
    RequirementRepository(session).advance(requirement_id, "awaiting_scope_confirmation")
    return ScopeProposed(proposal_id=proposal.id, log_id=run.log_id, visibility=payload["visibility"],
                         provider=payload["provider"], where_to_search=payload["where_to_search"],
                         reason=payload["reason"])


def pending_scope(session: Session, requirement_id: UUID):
    """The scope proposal waiting for the requester, if any (the newest pending one)."""
    pending = ProposalRepository(session).list_for_requirement(requirement_id, "scope", status="pending")
    return pending[-1] if pending else None


def proposed_scope(session: Session, requirement_id: UUID) -> ScopeProposed | None:
    """The pending scope as the requester sees it."""
    proposal = pending_scope(session, requirement_id)
    if proposal is None:
        return None
    p = proposal.payload
    return ScopeProposed(proposal_id=proposal.id, log_id=proposal.proposal_log_id, visibility=p["visibility"],
                         provider=p["provider"], where_to_search=p["where_to_search"], reason=p["reason"])


def decide_scope(session: Session, *, requirement_id: UUID, user: User, decision: Decision,
                 note: str | None = None) -> Scoped | ScopeNotConfirmed:
    requirements = RequirementRepository(session)
    requirement = requirements.lock(requirement_id)
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
