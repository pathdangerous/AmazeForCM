"""Step 05 glossary (phase 5 plan, assumptions 8 to 20; sections 5.1 to 5.3).

Outcomes (CLAUDE.md): proposed, duplicate_alias, conflict_to_owner; one per column that gets a proposal.
Not outcomes: a column left to step 06 (no answer), a skipped column, the owner's decision.

A run, in the background (apps/api/jobs.py runs these in this order, each in its own transaction):
1. queue_run(): a glossary_runs row for a registered feed, or the active one (one per feed);
2. claim_run(): one run, running, a new attempt and lease; committed at once;
3. builder_input(): read only; the new columns not covered, each with its candidates. None when there is
   nothing to ask (no call, no log row);
4. the caller ends the transaction; record_run(): Agent.run() calls the model with no connection held; in
   before_write the feed row, then the run row are locked and the run must still be running on this
   attempt (RunSuperseded otherwise: a discard row, nothing written); then the proposals, the run's results
   and done, one notification per glossary owner when proposals wait;
5. on any error: fail_run() (failed with the reason; not retried by itself).

decide_element(): the glossary owner approves one option (the recommended one unless another is chosen)
or rejects with a note. Lock order (assumption 49): the feed row, the proposal row, the glossary lock.
The step flushes; the caller commits. core/steps never imports agents: the agent is an argument.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.glossary import GlossaryElement, GlossaryElementDraft
from core.domain.mapping import (
    GLOSSARY_OUTCOMES,
    ElementOption,
    GlossaryBuilderInput,
    GlossaryProposalPayload,
    GlossaryRun,
    NewColumn,
)
from core.repositories.configuration import MappingRepository
from core.repositories.errors import (
    FeedNotProfiled,
    FeedNotRegistered,
    NotAnApprover,
    PhraseTaken,
    ProposalAlreadyDecided,
    WrongProposalKind,
)
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.glossary_runs import GlossaryRunRepository
from core.repositories.proposals import ProposalRepository
from core.steps.candidates import candidate_list, covered_by, new_columns
from core.steps.errors import ChoiceNeeded, ChoiceNotOffered
from core.steps.result import StepResult
from core.steps.s1_scope import RunsAgent
from ports.identity.base import User
from ports.notify.base import Notifier

SKIPPED = {"name": "covered_by_name", "mapping": "mapped", "asked": "already_asked"}
LEFT = "left_to_step_06"


class Proposed(StepResult):
    outcome: Literal["proposed"] = "proposed"
    column: str
    proposal_id: UUID


class DuplicateAlias(StepResult):
    outcome: Literal["duplicate_alias"] = "duplicate_alias"
    column: str
    element_key: str  # the existing element
    proposal_id: UUID


class ConflictToOwner(StepResult):
    outcome: Literal["conflict_to_owner"] = "conflict_to_owner"
    column: str
    clashes: list[str]  # the existing elements it clashes with
    proposal_id: UUID


GlossaryResult = Annotated[Proposed | DuplicateAlias | ConflictToOwner, Field(discriminator="outcome")]


class RunRecorded(BaseModel):
    """Not an outcome: what one run did. results are the typed outcomes; run.results holds, per column,
    the outcome, left_to_step_06, or why it was skipped."""

    model_config = ConfigDict(frozen=True)

    run: GlossaryRun
    results: list[GlossaryResult]


class ElementDecided(BaseModel):
    """Not an outcome: the glossary owner's decision. element is the version written, None for a rejection."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    decision: Literal["approved", "rejected"]
    element: GlossaryElement | None


class RunOnBanner(BaseModel):
    """One run as the banner shows it: its feed, and per outcome how many columns got it."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    feed_id: UUID
    feed_code: str
    feed_name: str
    status: str
    reason: str
    attempt: int
    created_at: datetime
    finished_at: datetime | None
    error: str | None
    counts: dict[str, int]


class RunsBanner(BaseModel):
    model_config = ConfigDict(frozen=True)

    active: list[RunOnBanner]
    finished: list[RunOnBanner]  # the last five, newest first
    waiting_proposals: int  # glossary_element proposals that wait for the glossary owner


@dataclass(frozen=True)
class BuilderPlan:
    run: GlossaryRun
    agent_input: GlossaryBuilderInput | None  # None: nothing to ask
    skipped: dict[str, str]


# ---------------------------------------------------------------- the run


def queue_run(session: Session, *, feed_id: UUID, reason: str, requested_by: str,
              columns: Sequence[str] = ()) -> tuple[GlossaryRun, bool]:
    """A run for a registered feed's latest profile, or the active one (and whether it was created)."""
    feed = FeedRepository(session).lock(feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}; the glossary builder runs on a registered feed")
    profile = FeedProfileRepository(session).latest(feed_id)
    if profile is None:
        raise FeedNotProfiled(f"Feed {feed.code!r} has no profile")
    return GlossaryRunRepository(session).queue(feed_id=feed_id, profile_id=profile.id, reason=reason,
                                                requested_by=requested_by, columns=columns)


def claim_run(session: Session, *, lease_seconds: int) -> GlossaryRun | None:
    return GlossaryRunRepository(session).claim(lease_seconds=lease_seconds)


def builder_input(session: Session, run: GlossaryRun) -> BuilderPlan:
    """Read only. The latest profile's new columns (assumption 8), less those covered (assumption 9)."""
    feed = FeedRepository(session).get(run.feed_id)
    profile = FeedProfileRepository(session).latest(run.feed_id)
    glossary = GlossaryRepository(session).list_current()
    mapped = {m.source_field for m in MappingRepository(session).current_for_feed(run.feed_id)}
    asked = {p.payload.get("source_field") for p in ProposalRepository(session).list_for_feed(run.feed_id,
                                                                                               "glossary_element")}
    skipped, columns = {}, []
    for column in new_columns(profile.columns, profile.spec, sent_back=run.columns):
        why = covered_by(column, glossary, mapped=mapped, asked=asked)
        if why is not None:
            skipped[column.name] = SKIPPED[why]
            continue
        also = (column.declared_as,) if column.declared_as else ()
        columns.append(column.model_copy(update={
            "candidates": candidate_list(column.name, column.type, glossary, also=also)}))
    if not columns:
        return BuilderPlan(run=run, agent_input=None, skipped=skipped)
    by_key = {e.key: e.domain for e in glossary}
    declared = Counter(by_key[k] for k in profile.spec.elements if k in by_key)
    domains = sorted({e.domain for e in glossary})
    source_domain = min(declared, key=lambda d: (-declared[d], d)) if declared else domains[0]
    return BuilderPlan(run=run, skipped=skipped, agent_input=GlossaryBuilderInput(
        run_id=run.id, feed_id=feed.id, source_code=feed.source_code or feed.code, source_name=feed.name,
        source_domain=source_domain, domains=domains, columns=columns))


def _still_running(session: Session, run: GlossaryRun) -> None:
    """before_write: the feed row, then the run row; the run must still be running on this attempt."""
    FeedRepository(session).lock(run.feed_id)
    GlossaryRunRepository(session).lock(run.id, attempt=run.attempt)


def _result(proposal) -> Proposed | DuplicateAlias | ConflictToOwner:
    payload = GlossaryProposalPayload(**proposal.payload)
    if payload.outcome == "proposed":
        return Proposed(column=payload.source_field, proposal_id=proposal.id)
    if payload.outcome == "duplicate_alias":
        return DuplicateAlias(column=payload.source_field, element_key=payload.options[0].key, proposal_id=proposal.id)
    return ConflictToOwner(column=payload.source_field, clashes=payload.clashes, proposal_id=proposal.id)


def record_run(session: Session, *, plan: BuilderPlan, agent: RunsAgent | None, notifier: Notifier,
               owners: Sequence[str]) -> RunRecorded:
    run, runs = plan.run, GlossaryRunRepository(session)
    if plan.agent_input is None:  # nothing to ask: no call, no log row (assumption 12)
        _still_running(session, run)
        return RunRecorded(run=runs.finish(run.id, results=dict(plan.skipped), proposal_log_id=None), results=[])
    called = agent.run(session, plan.agent_input, before_write=lambda s: _still_running(s, run))
    results = [_result(p) for p in called.proposals]
    outcomes = {r.column: r.outcome for r in results}
    by_column = {c.name: outcomes.get(c.name, LEFT) for c in plan.agent_input.columns}
    finished = runs.finish(run.id, results={**by_column, **plan.skipped}, proposal_log_id=called.log_id)
    if results:
        feed = FeedRepository(session).get(run.feed_id)
        for owner in owners:
            notifier.notify(recipient=owner, subject=f"Glossary proposals wait: {feed.code}",
                            body=f"The glossary builder proposed {len(results)} change(s) for feed {feed.code} "
                                 f"({feed.name}). Each waits for a glossary owner's decision.")
    return RunRecorded(run=finished, results=results)


def runs_banner(session: Session) -> RunsBanner:
    """Read only: what the backend-ui banner shows (plan, section 10)."""
    runs, feeds = GlossaryRunRepository(session), FeedRepository(session)

    def on_banner(run: GlossaryRun) -> RunOnBanner:
        feed = feeds.get(run.feed_id)
        counts = Counter(v for v in (run.results or {}).values() if v in GLOSSARY_OUTCOMES)
        return RunOnBanner(id=run.id, feed_id=feed.id, feed_code=feed.code, feed_name=feed.name, status=run.status,
                           reason=run.reason, attempt=run.attempt, created_at=run.created_at,
                           finished_at=run.finished_at, error=run.error, counts=dict(sorted(counts.items())))

    waiting = len(ProposalRepository(session).list(status="pending", kind="glossary_element"))
    return RunsBanner(active=[on_banner(r) for r in runs.list_active()],
                      finished=[on_banner(r) for r in runs.recent_finished()], waiting_proposals=waiting)


def fail_run(session: Session, *, run: GlossaryRun, error: str) -> GlossaryRun:
    """The run failed (assumption 19), unless another worker took it meanwhile (then nothing changes)."""
    runs = GlossaryRunRepository(session)
    runs.lock(run.id, attempt=run.attempt)  # RunSuperseded if the run is no longer this worker's
    return runs.fail(run.id, error=error)


# ---------------------------------------------------------------- the glossary owner's decision


def _chosen(payload: GlossaryProposalPayload, choice: int | None) -> ElementOption:
    if choice is None:
        if payload.recommended is None:
            raise ChoiceNeeded("This proposal recommends nothing: choose one of its options")
        choice = payload.recommended
    if not 0 <= choice < len(payload.options):
        raise ChoiceNotOffered(f"Option {choice} is not one this proposal offers ({len(payload.options)} options)")
    return payload.options[choice]


def decide_element(session: Session, *, proposal_id: UUID, user: User, decision: Literal["approved", "rejected"],
                   choice: int | None, note: str | None, notifier: Notifier,
                   stewards: Sequence[str]) -> ElementDecided:
    proposals = ProposalRepository(session)
    proposal = proposals.get(proposal_id)
    if proposal.kind != "glossary_element":
        raise WrongProposalKind(f"Proposal {proposal_id} is a {proposal.kind} proposal, not glossary_element")
    feed = FeedRepository(session).lock(proposal.feed_id)
    proposal = proposals.lock(proposal_id)
    if decision == "rejected":
        proposals.record_decision(proposal_id, user=user, decision="rejected", note=note)
        return ElementDecided(proposal_id=proposal_id, decision="rejected", element=None)
    if proposal.status != "pending":  # checked before the choice and the glossary lock
        raise ProposalAlreadyDecided(f"Proposal {proposal_id} is already {proposal.status}")
    if "glossary_owner" not in user.roles:
        raise NotAnApprover(f"Proposal {proposal_id} needs role glossary_owner; {user.id} has {user.roles}")
    payload = GlossaryProposalPayload(**proposal.payload)
    option = _chosen(payload, choice)
    glossary = GlossaryRepository(session)
    glossary.lock()
    if option.kind == "new_element":
        draft = option.element
    else:
        last = glossary.get_current(option.key)
        draft = GlossaryElementDraft(key=last.key, name=last.name, domain=last.domain, data_type=last.data_type,
                                     definition=last.definition, aliases=[*last.aliases, option.alias])
    taken = glossary.phrases_taken(draft)
    if taken or (option.kind == "new_element" and glossary.exists(draft.key)):
        named = ", ".join(f"{phrase} ({key})" for phrase, key in taken) or draft.key
        raise PhraseTaken(f"Another element already owns {named}")
    proposals.record_decision(proposal_id, user=user, decision="approved", note=note)
    element = glossary.add_version(proposal_id=proposal_id, created_by=user.id, option=option)
    for steward in stewards:
        notifier.notify(recipient=steward, subject=f"Can now be mapped: {payload.source_field} of {feed.code}",
                        body=f"Glossary element {element.key} (version {element.version}) was approved for column "
                             f"{payload.source_field} of feed {feed.code}. Propose its mapping.")
    return ElementDecided(proposal_id=proposal_id, decision="approved", element=element)
