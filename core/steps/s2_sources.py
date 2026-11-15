"""S2 source list (phase 3 plan, assumptions 30 to 32).

Outcomes (CLAUDE.md): sources_listed; no_fit.

Code finds, the agent ranks, the catalogue states the facts:
- candidates are the catalogue sources whose visibility and channel match the confirmed scope, that
  cover at least one requested element or ticked term, and that this requirement has not released;
- with no candidate, no model is called: no_fit (nothing_in_scope);
- otherwise the SourceDiscoveryAgent ranks them: one source_list proposal per listed source, for the
  requester; coverage and conditions in each proposal are copied from the catalogue;
- the agent listing none is no_fit (agent_listed_none).
sources_listed moves the requirement to awaiting_source_choice. no_fit moves it to no_source_found and
tells the backend team. The status is checked before the agent is called. The step flushes; the caller
commits. The agent is an argument (assumption 22).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.requirement import Requirement
from core.domain.sourcing import CandidateRef, Coverage, SourceDiscoveryInput, SourceSummary
from core.repositories.errors import RequirementNotInStatus
from core.repositories.proposals import ProposalRepository
from core.repositories.requirement_scopes import RequirementScopeRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.steps.result import StepResult
from core.steps.s1_scope import RunsAgent, locked_in
from ports.connectors.catalogue import SourceCatalogue, SourceEntry, UnknownSource
from ports.notify.base import Notifier


class SourceCandidate(BaseModel):
    """A listed source as the requester sees it: its proposal, the source and its conditions in force,
    what it covers, and the agent's reason."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    rank: int
    source: SourceSummary
    coverage: Coverage
    reason: str


class SourcesListed(StepResult):
    outcome: Literal["sources_listed"] = "sources_listed"
    candidates: list[SourceCandidate]


class NoFit(StepResult):
    outcome: Literal["no_fit"] = "no_fit"
    # no_channel_in_scope: S1 found no channel to search, so S2 never ran (review fix 5)
    reason: Literal["nothing_in_scope", "agent_listed_none", "no_channel_in_scope"]


SourcesResult = Annotated[SourcesListed | NoFit, Field(discriminator="outcome")]


def _phrase(text: str) -> str:
    return " ".join(text.lower().replace("_", " ").split())


def coverage(requirement: Requirement, entry: SourceEntry) -> Coverage:
    others = {_phrase(f) for f in entry.other_fields}
    elements = [k for k in requirement.elements if k in entry.elements]
    terms = [t for t in requirement.terms if _phrase(t) in others]
    missing = [k for k in requirement.elements if k not in elements] + [t for t in requirement.terms if t not in terms]
    return Coverage(elements=elements, terms=terms, missing=missing)


def listed_sources(session: Session, requirement_id: UUID, catalogue: SourceCatalogue) -> list[SourceCandidate]:
    """The sources the requester can still pick: pending source_list proposals, in rank order. The
    conditions shown are the catalogue's now (the ones a pick must accept, assumption 56)."""
    listed = []
    for proposal in ProposalRepository(session).list_for_requirement(requirement_id, "source_list", "pending"):
        payload = proposal.payload
        try:
            entry = catalogue.get(payload["source_code"])
        except UnknownSource:  # gone from the catalogue: it cannot be picked
            continue
        listed.append(SourceCandidate(proposal_id=proposal.id, rank=payload["rank"], source=SourceSummary.of(entry),
                                      coverage=Coverage.model_validate(payload["coverage"]),
                                      reason=payload["reason"]))
    return sorted(listed, key=lambda c: (c.rank, c.source.code))


FROM = ("scope_confirmed",)


def sources_input(session: Session, *, requirement_id: UUID, catalogue: SourceCatalogue) -> SourceDiscoveryInput | None:
    """Read only: the SourceDiscoveryAgent's input, or None when no source in scope covers anything (no
    model call is needed then). The caller ends the read transaction before the model call (review fix 2)."""
    requirement = RequirementRepository(session).get(requirement_id)
    if requirement.status not in FROM:
        raise RequirementNotInStatus(
            f"Requirement {requirement_id} is {requirement.status}; sources are listed from scope_confirmed")
    scope = RequirementScopeRepository(session).current(requirement_id)
    released = SourceChoiceRepository(session).released_sources(requirement_id)
    candidates = []
    for entry in catalogue.entries:
        if entry.visibility != scope.visibility or entry.channel not in scope.where_to_search:
            continue
        if entry.code in released:
            continue
        covered = coverage(requirement, entry)
        if covered.elements or covered.terms:
            candidates.append(CandidateRef(code=entry.code, name=entry.name, provider=entry.provider,
                                           description=entry.description, elements=entry.elements,
                                           other_fields=entry.other_fields, conditions=entry.conditions,
                                           coverage=covered))
    if not candidates:
        return None
    return SourceDiscoveryInput(
        requirement_id=requirement_id, requested_elements=requirement.elements, requested_terms=requirement.terms,
        scope_visibility=scope.visibility, where_to_search=scope.where_to_search, candidates=candidates)


def record_sources(session: Session, *, requirement_id: UUID, agent: RunsAgent,
                   agent_input: SourceDiscoveryInput | None, catalogue: SourceCatalogue, notifier: Notifier,
                   backend_team: Sequence[str]) -> SourcesListed | NoFit:
    """With an input: the model call (its call row committed at once by Agent.run()), then, with the
    requirement locked and its status checked again, the proposals. Without one: lock, check, no_fit. A
    status change during the call gives RequirementNotInStatus: the call gets a discard row, and nothing
    else is written."""
    requirements = RequirementRepository(session)

    def no_fit(requirement, reason: Literal["nothing_in_scope", "agent_listed_none"]) -> NoFit:
        requirements.advance(requirement_id, "no_source_found")
        scope = RequirementScopeRepository(session).current(requirement_id)
        for user_id in backend_team:
            notifier.notify(recipient=user_id, subject=f"No source fits: {requirement.title}",
                            body=f"No catalogue source fits requirement {requirement.id} ({requirement.title}) "
                                 f"in its {scope.visibility} scope ({reason}).")
        return NoFit(reason=reason)

    if agent_input is None:
        return no_fit(locked_in(session, requirement_id, FROM, "sources are listed"), "nothing_in_scope")
    locked = []
    run = agent.run(session, agent_input, before_write=lambda s: locked.append(
        locked_in(s, requirement_id, FROM, "sources are listed")))
    if not run.proposals:
        return no_fit(locked[0], "agent_listed_none")
    requirements.advance(requirement_id, "awaiting_source_choice")
    return SourcesListed(candidates=listed_sources(session, requirement_id, catalogue))
