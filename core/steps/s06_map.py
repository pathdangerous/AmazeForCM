"""Step 06 map (phase 5 plan, assumptions 21 to 33; sections 5.1 to 5.3).

Outcomes (CLAUDE.md): mapped (the steward's approval stored the mapping), tie_to_steward,
no_element_back_to_05. Not outcomes: a pending recommendation (MappingProposed: nobody decided), a
proposal not made because it would change nothing, a rejection.

The model-call pattern (assumption 25): mapping_input() reads; the API ends the transaction;
record_mappings() runs Agent.run(), whose before_write locks the feed row and checks again that the feed is
registered, that no input column got a pending proposal meanwhile (MappingAlreadyProposed) and that each
input column's current mapping and rejected elements are still what the input held (MappingChangedMeanwhile);
either is a discard row, nothing written. Then the proposals and, for the columns sent back that the glossary builder was
never asked about, one queued glossary run (reason sent_back), in the same transaction.

decide_mapping(): the data steward approves one candidate (the recommended one unless another is chosen; a
tie needs a choice) or rejects with a note. Lock order (assumption 49): the feed row, then the proposal row;
the next mapping version is read after the feed row is locked. The step flushes; the caller commits.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.configuration import Mapping
from core.domain.mapping import ColumnToMap, MappingInput, MappingProposalPayload
from core.repositories.configuration import MappingRepository
from core.repositories.errors import (
    ElementAlreadyMapped,
    FeedNotProfiled,
    FeedNotRegistered,
    MappingAlreadyProposed,
    MappingChangedMeanwhile,
    NotAnApprover,
    NothingToMap,
    ProposalAlreadyDecided,
    WrongProposalKind,
)
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from core.steps.candidates import candidate_list, kept_elements
from core.steps.errors import ChoiceNeeded, ChoiceNotOffered
from core.steps.result import StepResult
from core.steps.s05_glossary import queue_run
from core.steps.s1_scope import RunsAgent
from ports.identity.base import User


class Mapped(StepResult):
    outcome: Literal["mapped"] = "mapped"
    mapping: Mapping  # feed, source field, element, version, proposal


class TieToSteward(StepResult):
    outcome: Literal["tie_to_steward"] = "tie_to_steward"
    source_field: str
    candidates: list[str]
    proposal_id: UUID


class NoElementBackTo05(StepResult):
    outcome: Literal["no_element_back_to_05"] = "no_element_back_to_05"
    source_field: str
    reason: Literal["no_candidate", "type_does_not_fit", "every_candidate_rejected"]
    # the run queued (or joined) for it; None if the builder was already asked, or the column is mapped
    glossary_run_id: UUID | None


MapResult = Annotated[Mapped | TieToSteward | NoElementBackTo05, Field(discriminator="outcome")]


class MappingProposed(BaseModel):
    """Not an outcome: a pending proposal recommending one element."""

    model_config = ConfigDict(frozen=True)

    source_field: str
    element_key: str
    proposal_id: UUID


class MappingsProposed(BaseModel):
    """What one Propose mappings did."""

    model_config = ConfigDict(frozen=True)

    log_id: UUID
    recommended: list[MappingProposed]
    results: list[Annotated[TieToSteward | NoElementBackTo05, Field(discriminator="outcome")]]
    unchanged: list[str]  # a proposal would change nothing: not created (assumption 29)


class MappingRejected(BaseModel):
    """Not an outcome: the steward rejected the proposal; its elements are not proposed again."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    rejected: list[str]


# ---------------------------------------------------------------- propose


def mapping_input(session: Session, *, feed_id: UUID, column: str | None = None,
                  source_description: str | None = None) -> MappingInput:
    """Read only. Every profile column with no current mapping and no pending mapping proposal, or the one
    named (Propose again). Each with its candidates (the spec's hint among them, marked), less the elements
    the steward rejected for it, and its current element."""
    feed = FeedRepository(session).get(feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}; only a registered feed is mapped")
    profile = FeedProfileRepository(session).latest(feed_id)
    if profile is None:
        raise FeedNotProfiled(f"Feed {feed.code!r} has no profile")
    mappings = MappingRepository(session)
    current = {m.source_field: m.element_key for m in mappings.current_for_feed(feed_id)}
    proposals = ProposalRepository(session)
    if column is not None:
        chosen = [c for c in profile.columns if c.name == column]
        if not chosen:
            raise NothingToMap(f"Feed {feed.code!r} has no column {column!r} in its latest profile")
        if proposals.pending_for_column("mapping", feed_id, column) is not None:
            raise MappingAlreadyProposed(f"Column {column!r} of feed {feed.code!r} already has a pending proposal")
    else:
        chosen = [c for c in profile.columns if c.name not in current
                  and proposals.pending_for_column("mapping", feed_id, c.name) is None]
        if not chosen:
            raise NothingToMap(f"Feed {feed.code!r} has no column left to map")
    glossary = GlossaryRepository(session).list_current()
    columns = []
    for c in chosen:
        declared = profile.spec.columns.get(c.name)
        hint = declared if declared in profile.spec.elements else None
        rejected = mappings.rejected_elements(feed_id, c.name)
        offered = [x for x in candidate_list(c.name, c.type, glossary, hint=hint) if x.key not in rejected]
        columns.append(ColumnToMap(name=c.name, type=c.type, null_rate=c.null_rate, distinct_count=c.distinct_count,
                                   candidates=offered, current=current.get(c.name)))
    return MappingInput(feed_id=feed_id, source_code=feed.source_code or feed.code, source_name=feed.name,
                        source_description=source_description, columns=columns)


def _still_mappable(session: Session, agent_input: MappingInput) -> None:
    feed = FeedRepository(session).lock(agent_input.feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} became {feed.stage} while the agent answered")
    proposals, mappings = ProposalRepository(session), MappingRepository(session)
    current = {m.source_field: m.element_key for m in mappings.current_for_feed(feed.id)}
    for column in agent_input.columns:
        if proposals.pending_for_column("mapping", feed.id, column.name) is not None:
            raise MappingAlreadyProposed(f"Column {column.name!r} of feed {feed.code!r} got a pending mapping "
                                         "proposal while the agent answered")
        # a proposal made and decided during the call leaves nothing pending (independent review, finding 5)
        if current.get(column.name) != column.current:
            raise MappingChangedMeanwhile(f"Column {column.name!r} of feed {feed.code!r} was mapped to "
                                          f"{current.get(column.name)} while the agent answered")
        rejected = mappings.rejected_elements(feed.id, column.name) & {x.key for x in column.candidates}
        if rejected:  # the input left out every element rejected before it was built
            raise MappingChangedMeanwhile(f"For column {column.name!r} of feed {feed.code!r}, "
                                          f"{', '.join(sorted(rejected))} was rejected while the agent answered")


def _why_none(session: Session, feed_id: UUID, column: ColumnToMap) -> str:
    if not column.candidates and MappingRepository(session).rejected_elements(feed_id, column.name):
        return "every_candidate_rejected"
    if column.candidates and not any(x.type_fits for x in column.candidates):
        return "type_does_not_fit"
    return "no_candidate"


def record_mappings(session: Session, *, agent: RunsAgent, agent_input: MappingInput, user: User) -> MappingsProposed:
    run = agent.run(session, agent_input, before_write=lambda s: _still_mappable(s, agent_input))
    feed_id = agent_input.feed_id
    made = {p.payload["source_field"]: p for p in run.proposals}
    answers = {}
    for answer in run.output.answers:
        answers.setdefault(answer.column, answer.elements)
    recommended, results, unchanged, back = [], [], [], []
    for column in agent_input.columns:
        proposal = made.get(column.name)
        if proposal is not None:
            payload = MappingProposalPayload(**proposal.payload)
            if payload.recommended is not None:
                recommended.append(MappingProposed(source_field=column.name, element_key=payload.recommended,
                                                   proposal_id=proposal.id))
            else:
                results.append(TieToSteward(source_field=column.name, candidates=payload.candidates,
                                            proposal_id=proposal.id))
        elif kept_elements(column, answers.get(column.name, [])) == [column.current] and column.current:
            unchanged.append(column.name)
        else:
            back.append(column)
    asked = {p.payload.get("source_field") for p in ProposalRepository(session).list_for_feed(feed_id,
                                                                                              "glossary_element")}
    # not a column the builder was asked about, nor a mapped one (Propose again): the builder skips a mapped column
    to_send = [c.name for c in back if c.name not in asked and not c.current]
    run_id = None
    if to_send:  # assumption 26: in the same transaction
        glossary_run, _ = queue_run(session, feed_id=feed_id, reason="sent_back", requested_by=user.id,
                                    columns=to_send)
        run_id = glossary_run.id
    for column in back:
        results.append(NoElementBackTo05(source_field=column.name, reason=_why_none(session, feed_id, column),
                                         glossary_run_id=run_id if column.name in to_send else None))
    return MappingsProposed(log_id=run.log_id, recommended=recommended, results=results, unchanged=unchanged)


# ---------------------------------------------------------------- the steward's decision


def decide_mapping(session: Session, *, proposal_id: UUID, user: User, decision: Literal["approved", "rejected"],
                   choice: str | None, note: str | None) -> Mapped | MappingRejected:
    proposals = ProposalRepository(session)
    proposal = proposals.get(proposal_id)
    if proposal.kind != "mapping":
        raise WrongProposalKind(f"Proposal {proposal_id} is a {proposal.kind} proposal, not mapping")
    FeedRepository(session).lock(proposal.feed_id)  # before the proposal and the next version are read
    proposal = proposals.lock(proposal_id)
    if proposal.status != "pending":
        raise ProposalAlreadyDecided(f"Proposal {proposal_id} is already {proposal.status}")
    if "data_steward" not in user.roles:
        raise NotAnApprover(f"Proposal {proposal_id} needs role data_steward; {user.id} has {user.roles}")
    payload = MappingProposalPayload(**proposal.payload)
    if decision == "rejected":
        proposals.record_decision(proposal_id, user=user, decision="rejected", note=note)
        return MappingRejected(proposal_id=proposal_id, rejected=payload.candidates)
    if choice is None:
        if payload.recommended is None:
            raise ChoiceNeeded("This proposal is a tie: choose one of its candidates")
        choice = payload.recommended
    if choice not in payload.candidates:
        raise ChoiceNotOffered(f"{choice} is not a candidate of this proposal ({', '.join(payload.candidates)})")
    mappings = MappingRepository(session)
    holder = next((m.source_field for m in mappings.current_for_feed(proposal.feed_id)
                   if m.element_key == choice and m.source_field != payload.source_field), None)
    if holder is not None:
        raise ElementAlreadyMapped(f"Column {holder!r} of this feed is mapped to {choice}")
    proposals.record_decision(proposal_id, user=user, decision="approved", note=note)
    mapping = mappings.add_version(feed_id=proposal.feed_id, source_field=payload.source_field, element_key=choice,
                                   proposal_id=proposal_id)
    return Mapped(mapping=mapping)
