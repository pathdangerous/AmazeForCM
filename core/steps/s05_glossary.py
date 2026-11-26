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

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.glossary import GlossaryElement, GlossaryElementDraft
from core.domain.mapping import (
    GLOSSARY_OUTCOMES,
    ElementOption,
    GlossaryBuilderInput,
    GlossaryBuilderOutput,
    GlossaryProposalPayload,
    GlossaryRun,
    MappingProposalPayload,
    NewColumn,
    SeededAnswer,
    SeededOutput,
)
from core.repositories.configuration import MappingRepository
from core.repositories.errors import (
    AliasAlreadyOnElement,
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
from core.steps.candidates import candidate_list, covered_by, glossary_payloads, new_columns, phrases
from core.steps.errors import ChoiceNeeded, ChoiceNotOffered, needs_a_note
from core.steps.matching import words
from core.steps.result import StepResult
from core.steps.s02_profile import excluded_by_actions, selected_actions
from core.steps.s1_scope import RunsAgent
from core.steps.samples import samples_for_model
from ports.config import Settings
from ports.identity.base import User
from ports.notify.base import Notifier

UNITS_FILE = Path(__file__).resolve().parents[2] / "fixtures" / "glossary" / "units.json"
SKIPPED = {"name": "covered_by_name", "mapping": "mapped", "asked": "already_asked"}
LEFT = "left_to_step_06"
NOT_VALID = "skipped"  # "skipped: <why>": the column's option was not valid (its name gives no alias)


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
    # 7.22: the mapping proposal an approved request made for its column (None: not a request, or already decided)
    mapping_proposal_id: UUID | None = None


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


def builder_input(session: Session, run: GlossaryRun, settings: Settings | None = None) -> BuilderPlan:
    """Read only. The new columns (assumption 8) of the run's own profile (the feed's latest when the run
    was queued; 0017 checks it is the feed's), less those covered (assumption 9). A structure_change run (phase 7,
    step 12) looks only at its own columns, and none of them is sent back. Sample values (D3) reach the input only
    through samples_for_model(): none unless SEND_SAMPLE_VALUES is on, and then only for the columns asked."""
    feed = FeedRepository(session).get(run.feed_id)
    profile = FeedProfileRepository(session).get(run.profile_id)
    glossary = GlossaryRepository(session).list_current()
    mapped = {m.source_field for m in MappingRepository(session).current_for_feed(run.feed_id)}
    asked = {p.payload.get("source_field") for p in ProposalRepository(session).list_for_feed(run.feed_id,
                                                                                               "glossary_element")}
    skipped, columns = {}, []
    left_out = excluded_by_actions(session, run.feed_id)  # a selected exclude_column action: not asked about (T1)
    if run.reason in ("structure_change", "column_requested"):
        # only the run's own columns are asked about: step 12 parked them (none is sent back), or a person requested
        # them (7.22: each is "sent back" in the sense that the name rule does not skip it)
        looked_at = [c for c in profile.columns if c.name in set(run.columns)]
        sent_back = run.columns if run.reason == "column_requested" else ()
    else:
        looked_at, sent_back = profile.columns, run.columns
    for column in new_columns(looked_at, profile.spec, sent_back=sent_back):
        if column.name in left_out:
            skipped[column.name] = "left_out"
            continue
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
    asked = {c.name for c in columns}
    samples = samples_for_model([c for c in profile.columns if c.name in asked],
                                settings or Settings(_env_file=None, send_sample_values=False))
    return BuilderPlan(run=run, skipped=skipped, agent_input=GlossaryBuilderInput(
        run_id=run.id, feed_id=feed.id, source_code=feed.source_code or feed.code, source_name=feed.name,
        source_domain=source_domain, domains=domains, columns=columns, samples=samples,
        profile_actions=selected_actions(session, feed.id)))


@cache
def unit_seed() -> dict[str, str]:
    """fixtures/glossary/units.json (phase 7.8 plan, assumption 42): by element key, the unit of each ratio or
    percentage estimate, which the model cannot know from a column name. Read once."""
    return dict(json.loads(UNITS_FILE.read_text(encoding="utf-8"))["units"])


def seed_units(output: GlossaryBuilderOutput) -> SeededOutput:
    """after_answer (Agent.run, phase 6 plan, assumption 9): a copy of the answer in which a new element with no
    unit whose key the seed names gets the seed's unit, marked unit_source "seed". A unit the model gave is never
    overwritten. The call row keeps what the model said; only the proposals are made from this copy."""
    seed, answers = unit_seed(), []
    for answer in output.answers:
        element, source = answer.new_element, None
        if element is not None and element.unit is None and element.key in seed:
            element, source = element.model_copy(update={"unit": seed[element.key]}), "seed"
        answers.append(SeededAnswer(column=answer.column, same_as=answer.same_as, new_element=element,
                                    reason=answer.reason, unit_source=source))
    return SeededOutput(answers=answers)


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
    called = agent.run(session, plan.agent_input, before_write=lambda s: _still_running(s, run),
                       after_answer=seed_units)
    results = [_result(p) for p in called.proposals]
    outcomes = {r.column: r.outcome for r in results}
    _, not_valid = glossary_payloads(plan.agent_input, called.output)  # as the agent read the answer
    outcomes.update({column: f"{NOT_VALID}: {why}" for column, why in not_valid.items()})
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
    needs_a_note(decision, note)  # refused input, before any lock
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
        if words(option.alias) in phrases(last):  # before the draft: its validator would raise a bare error
            raise AliasAlreadyOnElement(f"{last.key} (version {last.version}) already has the name {option.alias}; "
                                        "there is nothing to add")
        draft = GlossaryElementDraft(key=last.key, name=last.name, domain=last.domain, data_type=last.data_type,
                                     definition=last.definition, aliases=[*last.aliases, option.alias], unit=last.unit)
    taken = glossary.phrases_taken(draft)
    if taken or (option.kind == "new_element" and glossary.exists(draft.key)):
        named = ", ".join(f"{phrase} ({key})" for phrase, key in taken) or draft.key
        raise PhraseTaken(f"Another element already owns {named}")
    proposals.record_decision(proposal_id, user=user, decision="approved", note=note)
    element = glossary.add_version(proposal_id=proposal_id, created_by=user.id, option=option)
    mapping = _mapping_for_request(session, proposal.feed_id, payload, element.key)
    for steward in stewards:
        notifier.notify(recipient=steward, subject=f"Can now be mapped: {payload.source_field} of {feed.code}",
                        body=f"Glossary element {element.key} (version {element.version}) was approved for column "
                             f"{payload.source_field} of feed {feed.code}. "
                             + ("Its mapping proposal waits for you." if mapping else "Propose its mapping."))
    return ElementDecided(proposal_id=proposal_id, decision="approved", element=element,
                          mapping_proposal_id=mapping.id if mapping else None)


def _mapping_for_request(session: Session, feed_id: UUID, payload: GlossaryProposalPayload, element_key: str):
    """Phase 7.22, plan D5. The element was requested for this column (its run is `column_requested`): the column's
    mapping proposal is made now, in the same transaction, by code: a manual proposal authored by the person who pressed
    Request, no model call, pending for the data steward. None for any other glossary proposal (the workbench path asks
    the MappingAgent as before) or when the column has been decided meanwhile."""
    run = GlossaryRunRepository(session).find(payload.run_id) if payload.run_id else None
    if run is None or run.reason != "column_requested":
        return None
    column, proposals = payload.source_field, ProposalRepository(session)
    left_out = any(p.payload.get("excluded") and p.payload["source_field"] == column
                   for p in proposals.list_for_feed(feed_id, "mapping", "approved"))
    if (left_out or proposals.pending_for_column("mapping", feed_id, column) is not None
            or any(m.source_field == column for m in MappingRepository(session).current_for_feed(feed_id))):
        return None
    profile = FeedProfileRepository(session).latest(feed_id)
    found = next((c for c in profile.columns if c.name == column), None) if profile else None
    body = MappingProposalPayload(source_field=column, column_type=found.type if found else None,
                                  candidates=[element_key], recommended=element_key,
                                  reason=f"{run.requested_by} requested a glossary element for this column; "
                                         f"{element_key} was approved for it.")
    return proposals.create_manual(kind="mapping", payload=body.model_dump(mode="json"),
                                   required_approver_roles=["data_steward"], feed_id=feed_id, author=run.requested_by)
