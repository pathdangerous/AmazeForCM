"""The onboarding workspace: the orchestration of one session (phase 7.8 plan, assumptions 31 to 39; task 5.3).

A session walks one feed through nine steps. Nothing is copied from the steps: every workspace step calls the step
functions that already exist (02 profile and its summary, 03 sign-off, 04 registration, 05 the glossary run, 06,
10, 09, 11 and the first load), and every decision is the decision those functions already make, by the named person
(CLAUDE.md rule 1). Rule 2 holds: the first load is the daily job's own fetch and load, with no model.

- start_session(): the session, the event `entered profile`, the profile job queued.
- enter_step(): the current step again (a failed or stale one); queues its job.
- run_job(): the worker body. It claims one job (a lease), refuses it when the onboarding's model calls reached the
  cap (`call_cap_reached`), runs the step's job with fresh sessions where the step functions need them (read, end the
  read, call, lock, write: no connection is held during a model call), counts the model calls it made, writes the
  events, and sets the step's state: needs_decision when a person must decide an item, else ready. An answer that
  arrives after the job's lease was taken again is discarded (the agent's `before_write` checks the job; the call row
  stays, with its discard row).
- approve_and_next(): refused (DecisionNeeded) while an item needs a decision; else approves every pending item as
  its step says, moves on and enters the next step. With four-eyes on, the rules wait for a second person.
- back(): refused after go-live is approved; marks the later steps stale and runs the step again.
- edit_item(): ProposalRepository.edit(), then the editor's acceptance.
- timeline() and workspace(): what the audit trail and the screen read.

Model calls are counted by a wrapper around each agent (the call is counted when it is made, so a call that failed
is counted too). Chosen over counting proposal_log rows because a failed call writes no row and the cap is about calls.

Confidence (assumption 35): confidence_of() gives each proposal a number or None. At or above
AUTO_ACCEPT_CONFIDENCE the item is shown as pre-accepted (an event by `system`); a person still approves it with
Approve & next. Below it, or None (a tie), the item needs a decision. An item the operator edits is accepted by the
editor (a `pre_accepted` event with the editor as actor).

Lock order of the calls that take locks here: the feed row, then the session row, then the proposal rows (the
workers' finishing transaction takes only the session row, so no cycle). Agents arrive as arguments (StepAgents): this
module never imports `agents` or `ports.llm`. Repositories flush; the caller commits (run_job commits its own).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from core.domain.manual import (
    ManualGlossary,
    ManualMapping,
    ManualNote,
    ManualProfileAction,
    ManualRule,
    ManualTransformation,
)
from core.domain.mapping import GlossaryProposalPayload, MappingProposalPayload
from core.domain.onboarding import (
    ONBOARDING_STEPS,
    AgentNote,
    ApproveOptions,
    CannotMapDecided,
    ConfidentApproved,
    InputFact,
    JobRun,
    OnboardingItem,
    OnboardingSession,
    StepAdvance,
    StepInputs,
    StepView,
    TimelineEntry,
    Workspace,
)
from core.domain.proposal import Proposal
from core.domain.rules import ProposedRule, RuleProposalPayload, RuleRecommenderInput
from core.domain.transformation import TransformationProposalPayload
from core.engine.run import execute_load, plan_load, record_load
from core.repositories.errors import (
    BackNotAllowed,
    ColumnAlreadyMapped,
    DecisionNeeded,
    FeedNotProfiled,
    FeedNotRegistered,
    ItemAlreadyPending,
    JobSuperseded,
    ManualItemRefused,
    NotACannotMapColumn,
    NotAnApprover,
    NothingToMap,
    NothingToTransform,
    OwnerNotADataOwner,
    ProposalEditRefused,
    RunSuperseded,
    SameApproverTwice,
    SourceFieldNotMapped,
    StepJobActive,
    StepNotEnterable,
    StepNotReady,
)
from core.repositories.certifications import LoadCertificationRepository
from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.glossary_runs import GlossaryRunRepository
from core.repositories.go_lives import GoLiveRepository
from core.steps.completion import completion_report
from core.steps.first_load_view import (
    expected_score, first_load_facts, latest_load_summary, load_breakdown, load_timeline,
)
from core.repositories.loads import LoadRepository
from core.repositories.onboarding import OnboardingJobRepository, OnboardingRepository
from core.repositories.products import ProductRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.standard_records import StandardRecordRepository
from core.repositories.transformations import TransformationRepository
from core.repositories.tables import ProposalLogEntry  # not core.proposal_log: that module imports ports.llm
from core.repositories.upload_sources import UploadSourceRepository
from core.steps.candidates import candidate_list
from core.steps.cannot_map import cannot_map_of
from core.steps.s02_profile import (
    decide_profile_action,
    profile_plan,
    record_profile,
    record_summary,
    run_profile,
    summary_input,
    summary_of,
)
from core.steps.s03_signoff import sign_off
from core.steps.s04_register import NoOwner, register_feed, revise_visibility
from core.steps.s05_glossary import builder_input, claim_run, decide_element, fail_run, queue_run, record_run
from core.steps.s06_map import decide_mapping, mapping_input, record_mappings
from core.steps.s09_rules import (
    RuleAwaitsChecker,
    backtest_for,
    check_manual_rule,
    decide_rule,
    record_rules,
    rules_input,
)
from core.steps.s10_transform import (
    decide_transformation,
    effective_key_policy,
    propose_transformations,
    transform_input,
)
from core.steps.s11_go_live import (
    TestFailedHeld,
    after_test_load,
    begin_test_load,
    decide_go_live,
    go_live_plan,
)
from core.steps.s11_go_live import record_summary as record_go_live_summary
from ports.config import Settings
from ports.connectors import get_connector
from ports.connectors.base import SourceConnector
from ports.connectors.catalogue import SourceCatalogue, UnknownSource
from ports.identity.base import Identity, User
from ports.notify import get_notifier
from ports.storage.base import BlobStore

log = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractContextManager[Session]]

# the steps a person may go Back to (the profile registers the feed, which cannot be undone)
BACKABLE: tuple[str, ...] = ("mapping", "glossary", "transformations", "rules", "go_live")
AGENT_STEPS: tuple[str, ...] = ("profile", "mapping", "glossary", "transformations", "rules", "go_live")
STEP_KINDS: dict[str, tuple[str, ...]] = {
    "profile": ("profile_summary", "profile_action"), "glossary": ("glossary_element",), "mapping": ("mapping",),
    "transformations": ("transformation",), "rules": ("rule",), "go_live": ("review_summary",)}
STEP_OF_KIND = {kind: step for step, kinds in STEP_KINDS.items() for kind in kinds}
AGENT_NAMES = {"profile": "ProfilerAgent", "glossary": "GlossaryBuilderAgent", "mapping": "MappingAgent",
               "transformations": "TransformationAgent", "rules": "RuleRecommenderAgent",
               "go_live": "ReviewSummariserAgent"}
# the steps whose items are proposals a person decides one by one ("Approve all confident" is offered at these)
ITEM_STEPS: tuple[str, ...] = ("mapping", "glossary", "transformations", "rules")
SECOND_PERSON = "a second person"
RERUN_NOTE = "rerun after Back"
MAX_GLOSSARY_RUNS = 20  # runs of other feeds that may sit in front of this feed's in the queue


# ---------------------------------------------------------------- what the orchestration is given


@dataclass(frozen=True)
class StepAgents:
    """The agents the steps call (anything with Agent.run()). Built by the API or the worker."""

    profiler: Any
    glossary_builder: Any
    mapper: Any
    transformer: Any
    rule_recommender: Any
    summariser: Any


@dataclass(frozen=True)
class OnboardingContext:
    """Everything a call needs besides the database session. `load_now` is the daily job's fetch-and-load of one feed
    (DailyFetch.fetch_and_load): core never imports apps."""

    agents: StepAgents
    blob_store: BlobStore
    settings: Settings
    identity: Identity
    load_now: Callable[[UUID], dict[str, str | None]] | None = None
    connector_factory: Callable[[str], SourceConnector] | None = None
    daily_run_utc: str = "06:00"  # what the go-live notice says; the daily job's hour

    def connector(self, name: str) -> SourceConnector:
        return (self.connector_factory or (lambda n: get_connector(n, self.settings)))(name)


# ---------------------------------------------------------------- small helpers


def _catalogue(session: Session, settings: Settings) -> SourceCatalogue:
    """The file catalogue and the uploaded sources (the same one the daily job reads)."""
    return SourceCatalogue.load(settings.source_catalogue, settings.allowed_source_hosts()).with_uploads(
        UploadSourceRepository(session).entries())


def _person(ctx: OnboardingContext, user_id: str) -> User:
    found = ctx.identity.get_user(user_id)
    return found or User(id=user_id, display_name=user_id, email=f"{user_id}@example.test", roles=["backend_team"])


def _with_role(ctx: OnboardingContext, role: str) -> list[str]:
    return [u.id for u in ctx.identity.list_users() if role in u.roles]


def _system(proposal: Proposal) -> User:
    """The user that rejects a pending proposal when its step runs again: it holds the roles the proposal asks for."""
    return User(id="system", display_name="System", email="system@example.test",
                roles=list(proposal.required_approver_roles))


def _next_step(step: str) -> str:
    return ONBOARDING_STEPS[ONBOARDING_STEPS.index(step) + 1]


def confidence_of(proposal: Proposal, scores: Mapping[str, Mapping[str, float]] | None = None) -> float | None:
    """How sure the agent is (assumption 35); None means a person must look. The payload's own `confidence` wins;
    a mapping is as sure as its chosen candidate's score (scores: column -> element -> score, from the agent's input),
    and a tie is None (the model left several candidates, or the top two scores are equal); a glossary conflict is 0.5;
    a profile summary that recommends rejecting the feed is None; anything else is certain (1.0)."""
    payload = proposal.payload
    if proposal.kind == "mapping":
        chosen = payload.get("recommended")
        column = (scores or {}).get(payload.get("source_field", ""))
        if chosen is None or not column or chosen not in column:
            return None
        top = sorted(column.values(), reverse=True)
        return None if len(top) > 1 and top[0] == top[1] else float(column[chosen])
    if proposal.kind == "profile_summary" and payload.get("recommended_outcome") == "rejected":
        return None
    if proposal.kind == "rule":
        # Task 5.3b: a rule is as sure as its back-test on the feed's own file says; a rule that flags rows of the file
        # being onboarded needs a person (it may be wrong for this data, e.g. "not negative" on a percent change).
        own = _current_file_delivery(payload)
        if own is not None:
            return 1.0 if own["flagged"] == 0 else round(1 - own["flagged"] / own["rows"], 2)
    if isinstance(payload.get("confidence"), int | float):
        return float(payload["confidence"])
    if proposal.kind == "glossary_element" and payload.get("outcome") == "conflict_to_owner":
        return 0.5
    return 1.0


def _current_file_delivery(payload: Mapping) -> dict | None:
    """The rule's back-test delivery on the feed's own profiled file (step 09, task 5.3b), when it was read."""
    for delivery in (payload.get("backtest") or {}).get("deliveries") or []:
        if delivery.get("label") == "current file" and delivery.get("excluded") is None and delivery.get("rows", 0) > 0:
            return delivery
    return None


def _built_reason(kind: str, p: Mapping[str, Any], confidence: float | None) -> str:
    """Task 7.3a: a sentence for a payload that carries no reason of its own, made from the payload's facts only."""
    field = p.get("source_field")
    if kind == "profile_summary":
        return " ".join(str(x) for x in (p.get("summary"), p.get("key_reason")) if x)
    if kind == "mapping":
        if p.get("recommended"):
            score = "" if confidence is None else f" (score {confidence:.2f})"
            return f"Name and type match {p['recommended']}{score}."
        names = list(p.get("candidates", []))
        count = "Two" if len(names) == 2 else str(len(names))
        return f"{count} candidates score the same: {', '.join(names)}."
    if kind == "glossary_element":
        if p.get("outcome") == "conflict_to_owner":
            return f"{field} clashes with {', '.join(p.get('clashes', []))}, so the glossary owner decides."
        option = (p.get("options") or [{}])[p.get("recommended") or 0]
        if option.get("kind") == "new_element":
            return f"No glossary element covers {field}, so {option.get('key')} is proposed as a new element."
        return f"{field} is another name for {option.get('key')}, so it is added as an alias."
    if kind == "transformation":
        ops = ", ".join(s.get("op", "?") for s in p.get("steps", [])) or "no steps"
        return f"{field} is cleaned with {ops} so its values load as the glossary expects."
    if kind == "rule":
        return f"{p.get('rule_key')} checks that {str(p.get('description') or 'the rule holds').rstrip('.').lower()}."
    return str(p.get("summary") or f"The agent made this {kind} proposal.")


def _describe(proposal: Proposal, confidence: float | None = None) -> tuple[str | None, str, str]:
    """(column or rule key, one line, the reason) for the workspace. The reason is the agent's own when the payload has
    one (a profile summary's is its summary and its key reason), else a sentence built from the payload's facts."""
    p, kind = proposal.payload, proposal.kind
    own = p.get("reason") if isinstance(p.get("reason"), str) else None
    reason = (own or "").strip() or _built_reason(kind, p, confidence)
    if kind == "profile_summary":
        reason = _built_reason(kind, p, confidence)
    if kind == "mapping":
        chosen = p.get("recommended")
        if p.get("excluded"):
            return p.get("source_field"), f"{p.get('source_field')}: left out (not loaded)", reason
        return p.get("source_field"), (f"{p.get('source_field')} -> {chosen}" if chosen
                                       else f"{p.get('source_field')}: tie between {', '.join(p.get('candidates', []))}"), reason
    if kind == "glossary_element":
        if p.get("outcome") == "conflict_to_owner":
            return p.get("source_field"), f"{p.get('source_field')}: clashes with {', '.join(p.get('clashes', []))}", reason
        option = (p.get("options") or [{}])[p["recommended"] or 0]
        what = f"new element {option.get('key')}" if option.get("kind") == "new_element" \
            else f"alias {option.get('alias')} of {option.get('key')}"
        return p.get("source_field"), f"{p.get('source_field')}: {what}", reason
    if kind == "transformation":
        steps = ", ".join(s.get("op", "?") for s in p.get("steps", [])) or "no steps"
        return p.get("source_field"), f"{p.get('source_field')}: {steps}", reason
    if kind == "rule":
        own = _current_file_delivery(p)
        flags = f" (flags {own['flagged']} of {own['rows']} rows of this file)" if own and own["flagged"] else ""
        return p.get("rule_key"), f"{p.get('rule_key')}: {p.get('description', '')}{flags}", reason
    if kind == "profile_action":
        who = "a person does it" if p.get("applied_by") == "manual" else "the agent applies it"
        cols = ", ".join(p.get("columns", []))
        return (p.get("columns") or [None])[0], f"{str(p.get('action', '')).replace('_', ' ')}: {cols or 'the feed'} ({who})", reason
    return None, str(p.get("summary") or kind), reason


# ---------------------------------------------------------------- items, as the workspace and the decisions see them


def _chain(session: Session, raw: str) -> list[Proposal]:
    return ProposalRepository(session).lineage(UUID(raw))


def _step_items(session: Session, sess: OnboardingSession, step: str) -> list[OnboardingItem]:
    """The items of a step: the proposals its agent made in its latest answer (`agent_done`), each as its edit chain's
    last proposal. needs_decision: pending, not accepted (by confidence or by an editor), and not waiting for a second
    person."""
    events = OnboardingRepository(session).events(sess.id)
    mine = [e for e in events if e.step == step]
    entered = max((i for i, e in enumerate(mine) if e.event == "entered"), default=-1)
    since = mine[entered + 1:]  # since the step was last entered
    if not any(e.event == "agent_done" for e in since):
        return []
    accepted = {e.proposal_id for e in events if e.event == "pre_accepted" and e.proposal_id is not None}
    proposals = ProposalRepository(session)
    items = []
    raws: dict[str, float | None] = {}  # proposal id -> the confidence its answer gave
    for e in since:
        if e.event == "agent_done":
            for raw in e.detail.get("proposal_ids", []):
                raws[raw] = e.detail.get("confidence", {}).get(raw)
        elif e.event == "item_added" and e.proposal_id is not None:  # T7: what people added sits beside them
            raws.setdefault(str(e.proposal_id), None)
    seen: set[UUID] = set()
    for raw, confidence in raws.items():
        chain = _chain(session, raw)
        tip = chain[-1]
        approvals = proposals.approvals(tip.id)
        if tip.id in seen:
            continue
        seen.add(tip.id)
        waiting = SECOND_PERSON if (tip.status == "pending" and approvals
                                    and len(approvals) < len(tip.required_approver_roles)) else None
        column, summary, reason = _describe(tip, confidence)
        is_accepted = tip.id in accepted
        items.append(OnboardingItem(
            proposal_id=tip.id, kind=tip.kind, column=column, summary=summary, status=tip.status,
            confidence=confidence, pre_accepted=is_accepted,
            needs_decision=tip.status == "pending" and not is_accepted and waiting is None, waiting_for=waiting,
            edited=tip.edited_from is not None, edited_by=tip.edited_by, edited_from=tip.edited_from, reason=reason,
            payload=tip.payload, origin=tip.origin, author=tip.author,
            decided_by=approvals[-1].decided_by if approvals else None,
            decided_at=approvals[-1].created_at if approvals else None))
    return items


def _settled_state(items: list[OnboardingItem]) -> str:
    return "needs_decision" if any(i.needs_decision or i.waiting_for for i in items) else "ready"


def _reject_pending(session: Session, sessions: OnboardingRepository, sess: OnboardingSession, step: str) -> None:
    """A step runs again: its pending proposals are rejected by `system` first, so the one-pending-per-column
    guards hold. Decided configuration is left alone (it stays versioned)."""
    proposals = ProposalRepository(session)
    for kind in STEP_KINDS.get(step, ()):
        for pending in proposals.list_for_feed(sess.feed_id, kind, "pending"):
            proposals.record_decision(pending.id, user=_system(pending), decision="rejected", note=RERUN_NOTE)
            sessions.add_event(sess.id, step=step, event="rejected", actor="system", proposal_id=pending.id,
                               detail={"note": RERUN_NOTE})


def _enter(session: Session, sess: OnboardingSession, step: str, *, actor: str, rerun: bool) -> None:
    sessions, jobs = OnboardingRepository(session), OnboardingJobRepository(session)
    if rerun:
        _reject_pending(session, sessions, sess, step)
    sessions.move_to(sess.id, step)
    sessions.set_step_state(sess.id, step, "agent_working")
    sessions.add_event(sess.id, step=step, event="entered", actor=actor)
    jobs.queue(session_id=sess.id, step=step)
    sessions.add_event(sess.id, step=step, event="agent_queued", actor=actor)


def approve_confident(session: Session, *, session_id: UUID, step: str, user: User,
                      ctx: OnboardingContext) -> ConfidentApproved:
    """"Approve all confident" of one step (phase 7.13, kept in 7.22): the person's own decision on each confident item of
    the current step, through the step's own decision function, one approval row per item (rule 1). Confident: pending,
    pre-accepted by confidence (or by its editor) and not waiting for a second person. The step does not move on.
    StepNotEnterable: not an item step, or not the step this onboarding is on; StepNotReady: no answer yet."""
    sess = _locked(session, session_id)
    if step not in ITEM_STEPS or sess.status == "done" or step != sess.current_step:
        raise StepNotEnterable(f"Approve all confident is for the current step, one of {', '.join(ITEM_STEPS)}; "
                               f"this onboarding is on {sess.current_step}, not {step}")
    if sess.step_states[step] not in ("ready", "needs_decision"):
        raise StepNotReady(f"{step} has no answer to approve ({sess.step_states[step]})")
    chosen = [i for i in _step_items(session, sess, step)
              if i.status == "pending" and i.pre_accepted and i.waiting_for is None]
    approved, _ = _approve(session, sess, step, chosen, user=user, ctx=ctx, options=ApproveOptions())
    sessions = OnboardingRepository(session)
    for proposal_id in approved:
        sessions.add_event(session_id, step=step, event="approved", actor=user.id, proposal_id=proposal_id,
                           detail={"by": "approve_all_confident"})
    sessions.set_step_state(session_id, step, _settled_state(_step_items(session, sessions.get(session_id), step)))
    return ConfidentApproved(step=step, approved=approved,
                             workspace=workspace(session, session_id=session_id, settings=ctx.settings))


def _locked(session: Session, session_id: UUID) -> OnboardingSession:
    """The feed row, then the session row, both fresh."""
    sessions = OnboardingRepository(session)
    FeedRepository(session).lock(sessions.get(session_id).feed_id)
    return sessions.lock(session_id)


# ---------------------------------------------------------------- start, enter, back


def start_session(session: Session, *, feed_id: UUID, user: User) -> OnboardingSession:
    """Called when an upload is confirmed (task 5.4): upload is done, the profile step is entered and its job queued.
    FeedNotFound, OnboardingAlreadyExists."""
    FeedRepository(session).get(feed_id)
    created = OnboardingRepository(session).create(feed_id=feed_id, created_by=user.id)
    _enter(session, created, "profile", actor=user.id, rerun=False)
    return OnboardingRepository(session).get(created.id)


def enter_step(session: Session, *, session_id: UUID, step: str, user: User) -> OnboardingSession:
    """Run the current step's agent again (after a failure, or a stale step). StepNotEnterable: not the current step,
    a step that is done, or a finished session. StepJobActive: the agent is already working."""
    sess = _locked(session, session_id)
    if sess.status == "done" or step != sess.current_step or step not in AGENT_STEPS + ("first_load",):
        raise StepNotEnterable(f"{step} is not the step this onboarding is on ({sess.current_step})")
    state = sess.step_states[step]
    if OnboardingJobRepository(session).active_for_session(session_id) is not None or state == "agent_working":
        raise StepJobActive(f"The agent is already working on {step}")
    if state == "done":
        raise StepNotEnterable(f"{step} is done; go Back to run it again")
    _enter(session, sess, step, actor=user.id, rerun=state == "stale")
    return OnboardingRepository(session).get(session_id)


def back(session: Session, *, session_id: UUID, to_step: str, user: User) -> OnboardingSession:
    """Go back to a step already reached: every later step that was entered becomes stale, the step's pending
    proposals are rejected by `system`, and its agent runs again. BackNotAllowed once the owner approved go-live."""
    sess = _locked(session, session_id)
    if sess.status == "done" or to_step not in BACKABLE:
        raise BackNotAllowed(f"Back goes to one of {', '.join(BACKABLE)}, on an unfinished onboarding; not to {to_step}")
    if ONBOARDING_STEPS.index(to_step) > ONBOARDING_STEPS.index(sess.current_step):
        raise BackNotAllowed(f"{to_step} has not been reached; this onboarding is on {sess.current_step}")
    if GoLiveRepository(session).latest(sess.feed_id) is not None:
        # go-live pins the configuration. The one way out: the first load did not release (held, quarantined, failed)
        # and the feed has no product yet; then the rules may be redone and the owner approves a next go-live version
        unreleased = (sess.current_step == "first_load" and sess.step_states["first_load"] == "failed"
                      and ProductRepository(session).for_feed(sess.feed_id) is None)
        if to_step != "rules" or not unreleased:
            raise BackNotAllowed("The owner approved go-live: the configuration is pinned and nothing goes back "
                                 "(only a first load that did not release goes back, and only to the rules)")
    sessions, jobs = OnboardingRepository(session), OnboardingJobRepository(session)
    active = jobs.active_for_session(session_id)
    if active is not None:
        jobs.discard(active.id)  # its answer is no longer wanted: the lease check discards it
    sessions.add_event(session_id, step=to_step, event="back", actor=user.id, detail={"from": sess.current_step})
    for later in ONBOARDING_STEPS[ONBOARDING_STEPS.index(to_step) + 1:]:
        if sess.step_states[later] != "waiting":
            sessions.set_step_state(session_id, later, "stale")
            sessions.add_event(session_id, step=later, event="stale", actor=user.id)
    _enter(session, sess, to_step, actor=user.id, rerun=True)
    return sessions.get(session_id)


# ---------------------------------------------------------------- the worker


class _Counted:
    """Wraps an agent: counts each call, and before the answer is written checks the job is still ours."""

    def __init__(self, agent: Any, calls: list[int], still_ours: Callable[[Session], None]):
        self._agent, self._calls, self._still_ours = agent, calls, still_ours

    def run(self, session: Session, agent_input: Any, *, before_write=None, after_answer=None):
        self._calls[0] += 1

        def guarded(s: Session) -> None:
            if before_write is not None:
                before_write(s)
            self._still_ours(s)  # JobSuperseded: Agent.run() logs the discard and writes nothing

        extra = {} if after_answer is None else {"after_answer": after_answer}
        return self._agent.run(session, agent_input, before_write=guarded, **extra)


@dataclass
class _Work:
    """What one step's job produced."""

    scores: dict[str, dict[str, float]] = field(default_factory=dict)  # mapping: column -> element -> score
    nothing_to_do: str | None = None
    failed: str | None = None  # the step ends failed with this (not an exception: a held test load, a held first load)
    moves_on: bool = False  # the first load released: the product exists
    cannot_map: dict[str, str] = field(default_factory=dict)  # mapping: column -> why no existing element fits (7.22)


@dataclass
class _Env:
    sf: SessionFactory
    ctx: OnboardingContext
    session_id: UUID
    feed_id: UUID
    created_by: str
    agents: StepAgents

    def read(self, work: Callable[[Session], Any]) -> Any:
        """Read, then end the transaction, so no connection is held afterwards."""
        with self.sf() as s:
            try:
                out = work(s)
            except BaseException:
                s.rollback()
                raise
            s.commit()
            return out

    def write(self, work: Callable[[Session], Any]) -> Any:
        return self.read(work)


def run_job(session_factory: SessionFactory, *, ctx: OnboardingContext, lease_seconds: int = 300) -> JobRun | None:
    """Claim the oldest queued job (or one whose lease passed) and run it; None when there is none. Never raises for a
    failed step: the step is `failed` with the reason and the job `failed`; re-enter the step to try again."""
    with session_factory() as s:
        job = OnboardingJobRepository(s).claim(lease_seconds=lease_seconds)
        sess = OnboardingRepository(s).get(job.session_id) if job is not None else None
        s.commit()
    if job is None or sess is None:
        return None
    step = job.step
    calls = [0]

    def still_ours(s: Session) -> None:
        now = OnboardingJobRepository(s).get(job.id)
        if now.status != "running" or now.attempt != job.attempt:
            raise JobSuperseded(f"Onboarding job {job.id} is {now.status} on attempt {now.attempt}, not running on "
                                f"{job.attempt}")

    def counted(agent: Any) -> Any:
        return _Counted(agent, calls, still_ours)

    a = ctx.agents
    agents = StepAgents(profiler=counted(a.profiler), glossary_builder=counted(a.glossary_builder),
                        mapper=counted(a.mapper), transformer=counted(a.transformer),
                        rule_recommender=counted(a.rule_recommender), summariser=counted(a.summariser))
    env = _Env(sf=session_factory, ctx=ctx, session_id=sess.id, feed_id=sess.feed_id, created_by=sess.created_by,
               agents=agents)
    cap = ctx.settings.max_model_calls_per_onboarding

    with session_factory() as s:  # the cap, then `agent_started`
        sessions = OnboardingRepository(s)
        locked = sessions.lock(sess.id)
        if step in AGENT_STEPS and locked.model_calls >= cap:
            message = f"The call cap of {cap} model calls for this onboarding is reached; the agent was not asked."
            sessions.add_event(sess.id, step=step, event="call_cap_reached", actor="system",
                               detail={"cap": cap, "model_calls": locked.model_calls, "message": message})
            sessions.set_step_state(sess.id, step, "failed")
            OnboardingJobRepository(s).discard(job.id)
            s.commit()
            return JobRun(job_id=job.id, session_id=sess.id, step=step, status="discarded", detail=message)
        sessions.add_event(sess.id, step=step, event="agent_started", actor="system", detail={"attempt": job.attempt})
        s.commit()

    try:
        work = _STEP_JOBS[step](env)
    except JobSuperseded as exc:
        log.warning("onboarding job %s: its answer came after its lease was taken again; discarded (%s)", job.id, exc)
        _count_calls(session_factory, sess.id, calls[0])
        return JobRun(job_id=job.id, session_id=sess.id, step=step, status="superseded", detail=str(exc),
                      model_calls=calls[0])
    except Exception as exc:
        if not hasattr(exc, "reason") and type(exc).__module__.split(".")[0] not in ("core",):
            log.exception("onboarding job %s (%s) failed", job.id, step)
        return _fail_job(session_factory, job, sess, step, calls[0], exc)
    return _finish_job(session_factory, ctx, job, sess, step, calls[0], work)


def _count_calls(sf: SessionFactory, session_id: UUID, calls: int) -> None:
    if calls:
        with sf() as s:
            OnboardingRepository(s).bump_model_calls(session_id, calls)
            s.commit()


def _fail_job(sf: SessionFactory, job, sess: OnboardingSession, step: str, calls: int, exc: Exception) -> JobRun:
    reason = getattr(exc, "reason", None) or type(exc).__name__
    message = f"{AGENT_NAMES.get(step, step)} could not finish: {reason}" + (
        f" ({exc})" if str(exc) and str(exc) != reason else "")
    return _record_failure(sf, job, sess, step, calls, reason=reason, error=type(exc).__name__, message=message)


def _record_failure(sf: SessionFactory, job, sess: OnboardingSession, step: str, calls: int, *, reason: str, error: str,
                    message: str) -> JobRun:
    with sf() as s:
        sessions = OnboardingRepository(s)
        sessions.lock(sess.id)
        try:
            OnboardingJobRepository(s).fail(job.id, attempt=job.attempt, error=message)
        except JobSuperseded:
            s.rollback()
            return JobRun(job_id=job.id, session_id=sess.id, step=step, status="superseded", detail=message,
                          model_calls=calls)
        if calls:
            sessions.bump_model_calls(sess.id, calls)
        sessions.add_event(sess.id, step=step, event="agent_failed", actor="system",
                           detail={"reason": reason, "error": error, "message": message})
        sessions.set_step_state(sess.id, step, "failed")
        s.commit()
    return JobRun(job_id=job.id, session_id=sess.id, step=step, status="failed", detail=message, model_calls=calls)


def _finish_job(sf: SessionFactory, ctx: OnboardingContext, job, sess: OnboardingSession, step: str, calls: int,
                work: _Work) -> JobRun:
    if work.failed is not None:
        return _record_failure(sf, job, sess, step, calls, reason="step_failed", error="StepFailed",
                               message=work.failed)
    with sf() as s:
        sessions, proposals = OnboardingRepository(s), ProposalRepository(s)
        sessions.lock(sess.id)
        try:
            OnboardingJobRepository(s).finish(job.id, attempt=job.attempt)
        except JobSuperseded:
            s.rollback()
            return JobRun(job_id=job.id, session_id=sess.id, step=step, status="superseded", model_calls=calls)
        if calls:
            sessions.bump_model_calls(sess.id, calls)
        if step == "first_load":
            sessions.set_step_state(sess.id, "first_load", "done")
            sessions.add_event(sess.id, step="first_load", event="done", actor="system")
            sessions.move_to(sess.id, "product_ready")
            sessions.set_step_state(sess.id, "product_ready", "done")
            sessions.add_event(sess.id, step="product_ready", event="entered", actor="system")
            sessions.add_event(sess.id, step="product_ready", event="done", actor="system")
            sessions.finish(sess.id)
            s.commit()
            return JobRun(job_id=job.id, session_id=sess.id, step=step, status="done", model_calls=calls)
        pending = [p for kind in STEP_KINDS[step] for p in proposals.list_for_feed(sess.feed_id, kind, "pending")]
        if step == "profile":  # one summary per profile; the latest is the item, with the actions proposed beside it
            summaries = [p for p in pending if p.kind == "profile_summary"][-1:]
            wanted = {s.payload["profile_id"] for s in summaries}
            pending = summaries + [p for p in pending
                                   if p.kind == "profile_action" and p.payload.get("profile_id") in wanted]
        confidence = {str(p.id): confidence_of(p, work.scores) for p in pending}
        for p in pending:
            value = confidence[str(p.id)]
            if value is not None and value >= ctx.settings.auto_accept_confidence:
                sessions.add_event(sess.id, step=step, event="pre_accepted", actor="system", proposal_id=p.id,
                                   detail={"confidence": value, "threshold": ctx.settings.auto_accept_confidence})
        sessions.add_event(sess.id, step=step, event="agent_done", actor="system",
                           detail={"proposal_ids": [str(p.id) for p in pending], "confidence": confidence,
                                   "model_calls": calls, "nothing_to_do": work.nothing_to_do,
                                   **({"cannot_map": work.cannot_map} if step == "mapping" else {})})
        items = _step_items(s, sessions.get(sess.id), step)
        sessions.set_step_state(sess.id, step, _settled_state(items))
        s.commit()
    return JobRun(job_id=job.id, session_id=sess.id, step=step, status="done", model_calls=calls,
                  detail=work.nothing_to_do)


# ---------------------------------------------------------------- the jobs, one per step: the existing step functions


def _job_profile(env: _Env) -> _Work:
    feed_id, ctx = env.feed_id, env.ctx

    def where(s: Session):
        profile = FeedProfileRepository(s).latest(feed_id)
        return profile, (summary_of(s, feed_id=feed_id, profile_id=profile.id) if profile else None)

    profile, summary = env.read(where)
    if profile is None:  # step 02: read, end the read, DuckDB with no connection, lock and write
        plan = env.read(lambda s: profile_plan(s, feed_id=feed_id, catalogue=_catalogue(s, ctx.settings)))
        attempt = run_profile(plan, blob_store=ctx.blob_store)
        env.write(lambda s: record_profile(s, attempt, profiled_by=env.created_by))
        summary = None
    if summary is None:  # the ProfilerAgent's summary; a retry after a failed call profiles nothing twice
        agent_input = env.read(lambda s: summary_input(s, feed_id=feed_id, settings=ctx.settings))
        env.write(lambda s: record_summary(s, agent=env.agents.profiler, agent_input=agent_input,
                                           blob_store=ctx.blob_store))
    return _Work()


def _job_glossary(env: _Env) -> _Work:
    feed_id, ctx = env.feed_id, env.ctx
    for _ in range(MAX_GLOSSARY_RUNS):
        active = env.read(lambda s: GlossaryRunRepository(s).active(feed_id))
        if active is None:
            return _Work(nothing_to_do="No glossary element was requested")
        run = env.write(lambda s: claim_run(s, lease_seconds=ctx.settings.glossary_run_lease_seconds))
        if run is None:
            raise StepNotReady("The glossary builder is busy with this feed's run; enter the step again in a moment")
        _run_glossary(env, run)
    raise StepNotReady("The glossary builder has other runs to do first; enter the step again in a moment")


def _run_glossary(env: _Env, run) -> None:
    """GlossaryRuns._run_one() (apps/api/jobs.py) for one claimed run, with this job's counted agent."""
    ctx = env.ctx
    try:
        plan = env.read(lambda s: builder_input(s, run, settings=ctx.settings))  # D3: samples only if the setting is on
        env.write(lambda s: record_run(s, plan=plan, agent=env.agents.glossary_builder,
                                       notifier=get_notifier(ctx.settings, session=s),
                                       owners=_with_role(ctx, "glossary_owner")))
    except RunSuperseded as exc:  # another worker took the run after its lease: this answer is not used
        raise JobSuperseded(str(exc)) from exc
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        try:
            env.write(lambda s: fail_run(s, run=run, error=error))
        except RunSuperseded:
            pass
        raise


def _description(catalogue: SourceCatalogue, source_code: str | None) -> str | None:
    try:
        return catalogue.get(source_code).description if source_code else None
    except UnknownSource:
        return None


def _job_mapping(env: _Env) -> _Work:
    feed_id, ctx = env.feed_id, env.ctx

    def build(s: Session):
        feed = FeedRepository(s).get(feed_id)
        return mapping_input(s, feed_id=feed_id,
                             source_description=_description(_catalogue(s, ctx.settings), feed.source_code))

    try:
        agent_input = env.read(build)
    except NothingToMap as exc:  # every column is mapped or waits for the steward
        return _Work(nothing_to_do=str(exc))
    user = _person(ctx, env.created_by)
    made = env.write(lambda s: record_mappings(s, agent=env.agents.mapper, agent_input=agent_input, user=user,
                                               send_back=False))  # 7.22: a column no element fits is for a person
    return _Work(scores={c.name: {x.key: x.score for x in c.candidates} for c in agent_input.columns},
                 cannot_map={r.source_field: r.reason for r in made.results if r.outcome == "no_element_back_to_05"})


def _job_transformations(env: _Env) -> _Work:
    feed_id, ctx = env.feed_id, env.ctx
    try:
        agent_input = env.read(lambda s: transform_input(s, feed_id=feed_id, settings=ctx.settings))
    except NothingToTransform as exc:
        return _Work(nothing_to_do=str(exc))
    env.write(lambda s: propose_transformations(s, agent=env.agents.transformer, agent_input=agent_input))
    return _Work()


def _job_rules(env: _Env) -> _Work:
    feed_id, ctx = env.feed_id, env.ctx
    try:
        agent_input = env.read(lambda s: rules_input(s, feed_id=feed_id, catalogue=_catalogue(s, ctx.settings)))
    except NothingToMap as exc:
        return _Work(nothing_to_do=str(exc))
    backtest = backtest_for(agent_input, connector_factory=ctx.connector, blob_store=ctx.blob_store,
                            rule_timeout_seconds=ctx.settings.rule_timeout_seconds)  # runs after the call, no session
    env.write(lambda s: record_rules(s, agent=env.agents.rule_recommender, agent_input=agent_input, backtest=backtest))
    return _Work()


def _job_go_live(env: _Env) -> _Work:
    """Step 11 prepare, as the API runs it: the checks, a test load through the engine (plan, execute with no session,
    locked write), then a held notice or the ReviewSummariserAgent's summary."""
    feed_id, ctx = env.feed_id, env.ctx
    team, operations = _with_role(ctx, "backend_team"), _with_role(ctx, "operations")

    def owner_is_data_owner(owner_id: str) -> bool:
        owner = ctx.identity.get_user(owner_id)
        return owner is not None and "data_owner" in owner.roles

    plan = env.write(lambda s: go_live_plan(s, feed_id=feed_id, owner_is_data_owner=owner_is_data_owner,
                                            notifier=get_notifier(ctx.settings, session=s), backend_team=team))
    load = env.write(lambda s: begin_test_load(s, plan, stale_minutes=ctx.settings.load_stale_minutes,
                                               notifier=get_notifier(ctx.settings, session=s), operations=operations))
    engine_plan = env.read(lambda s: plan_load(s, load.id))
    outcome = execute_load(engine_plan, blob_store=ctx.blob_store, rule_timeout_seconds=ctx.settings.rule_timeout_seconds)
    load_run = env.write(lambda s: record_load(s, engine_plan, outcome))
    after = env.write(lambda s: after_test_load(s, plan=plan, load_run=load_run,
                                                notifier=get_notifier(ctx.settings, session=s), backend_team=team))
    if isinstance(after, TestFailedHeld):
        return _Work(failed=f"The test load failed at {after.stage}: {after.reason}. The feed is held; nothing was "
                            "proposed.")
    env.write(lambda s: record_go_live_summary(s, agent=env.agents.summariser, agent_input=after))
    return _Work()


def _job_first_load(env: _Env) -> _Work:
    """Load now: the daily job's own fetch of the newest drop-folder file, then steps 12, 13, 15 and 20."""
    if env.ctx.load_now is None:
        raise StepNotReady("No loader is configured for the first load")
    result = env.ctx.load_now(env.feed_id)
    if result.get("fetch") != "fetched":
        return _Work(failed=f"The first file was not fetched: {result.get('fetch')}.")
    product = env.read(lambda s: ProductRepository(s).for_feed(env.feed_id))
    if result.get("load") == "released" and product is not None:
        return _Work(moves_on=True)
    why = {"held": "its trust score is below the release threshold, so the load is held and its consumers are warned",
           "failed_rolled_back": "the file failed and was rolled back", "quarantined": "the same file was loaded before",
           }.get(result.get("load") or "", "the load did not release")
    return _Work(failed=f"The first load ended {result.get('load')}: {why}. The feed's product was not created.")


_STEP_JOBS: dict[str, Callable[[_Env], _Work]] = {
    "profile": _job_profile, "glossary": _job_glossary, "mapping": _job_mapping,
    "transformations": _job_transformations, "rules": _job_rules, "go_live": _job_go_live,
    "first_load": _job_first_load}


# ---------------------------------------------------------------- Approve & next


def approve_and_next(session: Session, *, session_id: UUID, user: User, ctx: OnboardingContext,
                     options: ApproveOptions | None = None) -> StepAdvance:
    """Approve every pending item of the current step as the step says (the operator's own decisions, the existing
    decision functions), then move on and enter the next step. StepNotReady: the agent has not answered. DecisionNeeded:
    an item needs a person's decision first. With four-eyes on, the rules wait for a second person: nothing moves, and
    the second person's own Approve & next finishes the step. A visibility in `options` is accepted only at profile and
    at go-live with a pending item (StepNotReady otherwise); at go-live only the registration's owner may change it
    (NotAnApprover)."""
    options = options or ApproveOptions()
    sess = _locked(session, session_id)
    step = sess.current_step
    if sess.status == "done" or sess.step_states[step] not in ("ready", "needs_decision"):
        raise StepNotReady(f"{step} has no answer to approve ({sess.step_states[step]})")
    if options.visibility is not None and step not in ("profile", "go_live"):
        raise StepNotReady("the visibility is chosen at go-live")  # refused input first, before any decision is asked for
    items = _step_items(session, sess, step)
    blocking = [] if step == "glossary" else [i for i in items if i.needs_decision]  # D6: the glossary step never blocks
    if blocking:
        raise DecisionNeeded(f"{len(blocking)} item(s) of {step} need a decision first: "
                             + "; ".join(i.summary for i in blocking), blocking)
    undecided = _undecided_columns(session, sess, step)
    if undecided:
        raise DecisionNeeded(_cannot_map_message(undecided))
    approved, waiting = _approve(session, sess, step, items, user=user, ctx=ctx, options=options)
    sessions = OnboardingRepository(session)
    for proposal_id in approved:
        sessions.add_event(session_id, step=step, event="approved", actor=user.id, proposal_id=proposal_id)
    if waiting:
        sessions.set_step_state(session_id, step, "needs_decision")
        return StepAdvance(moved=False, current_step=step, approved=approved, waiting_for=SECOND_PERSON)
    sessions.set_step_state(session_id, step, "done")
    sessions.add_event(session_id, step=step, event="done", actor=user.id)
    following = _next_step(step)
    _enter(session, sessions.get(session_id), following, actor=user.id, rerun=sess.step_states[following] == "stale")
    return StepAdvance(moved=True, current_step=following, approved=approved)


def show_requested_mapping(session: Session, *, session_id: UUID, result: Any, user: User) -> None:
    """The glossary owner's approval of a requested element made the column's mapping proposal (decide_element(), plan D5).
    Here it becomes an item of the Glossary requests step (`item_added`), so the step shows it and its Approve & next
    decides it. Nothing for any other decision. Called by the step's own approval and by the item decision route."""
    mapping_id = getattr(result, "mapping_proposal_id", None)
    if mapping_id is not None:
        OnboardingRepository(session).add_event(session_id, step="glossary", event="item_added", actor=user.id,
                                                proposal_id=mapping_id,
                                                detail={"kind": "mapping", "via": "glossary_approval"})


def _undecided_columns(session: Session, sess: OnboardingSession, step: str) -> list[str]:
    """The mapping step's cannot-map columns nobody has chosen Ignore or Request for (phase 7.22)."""
    return [c.column for c in cannot_map_of(session, sess) if c.state == "undecided"] if step == "mapping" else []


def _cannot_map_message(columns: list[str]) -> str:
    n = len(columns)
    return (f"{n} column{' ' if n == 1 else 's '}cannot be mapped: no glossary element fits "
            f"({', '.join(columns[:5])}{', ...' if n > 5 else ''}). Choose Ignore or Request glossary element for each.")


def decide_cannot_map(session: Session, *, session_id: UUID, action: str, columns: list[str], user: User,
                      settings: Settings | None = None) -> CannotMapDecided:
    """A person's choice for cannot-map columns of the mapping step (phase 7.22, plan D3 and D4).
    `ignore`: the existing excluded mapping, one manual proposal per column (author the person, no model call), approved
    with the step like any item; the column is not loaded.
    `request`: one glossary run for those columns only (reason `column_requested`, requested by the person, no model call
    here: the builder runs in the background or in the Glossary requests step) and the event `glossary_requested`.
    NotACannotMapColumn (409): a column that is not an undecided cannot-map column of this onboarding's mapping step (or a
    request the glossary owner rejected, which can only be Ignored); StepNotReady (409): the builder is working on this
    feed's run, ask again in a moment; StepNotEnterable, NotAnApprover from the step it goes through."""
    if action not in ("ignore", "request"):
        raise ValueError(f"{action!r} is not a choice for a cannot-map column")
    sess, _ = _step_for_adding(session, session_id, "mapping")
    wanted = list(dict.fromkeys(columns))
    undecided = {c.column: c for c in cannot_map_of(session, sess) if c.state == "undecided"}
    unknown = [c for c in wanted if c not in undecided or (action == "request" and undecided[c].glossary_status)]
    if not wanted or unknown:
        raise NotACannotMapColumn("Not an undecided cannot-map column of the mapping step (a request the owner rejected "
                                  "can only be ignored): " + (", ".join(unknown) or "no column named"))
    if action == "ignore":
        made = [add_item(session, session_id=session_id, user=user, item=ManualMapping(
            kind="mapping", source_field=column, excluded=True,
            reason="Ignored: no glossary element fits this column.")) for column in wanted]
        return CannotMapDecided(action="ignore", columns=wanted, proposals=made,
                                workspace=workspace(session, session_id=session_id, settings=settings))
    run, _ = queue_run(session, feed_id=sess.feed_id, reason="column_requested", requested_by=user.id, columns=wanted)
    if not set(wanted) <= set(run.columns):  # the active run is running: its columns are fixed
        raise StepNotReady("The glossary builder is working on this feed's run; ask for these columns again in a moment")
    OnboardingRepository(session).add_event(session_id, step="mapping", event="glossary_requested", actor=user.id,
                                            detail={"columns": wanted, "run_id": str(run.id)})
    return CannotMapDecided(action="request", columns=wanted, proposals=[], run_id=run.id,
                            workspace=workspace(session, session_id=session_id, settings=settings))


def _approve(session: Session, sess: OnboardingSession, step: str, items: list[OnboardingItem], *, user: User,
             ctx: OnboardingContext, options: ApproveOptions) -> tuple[list[UUID], int]:
    """(proposals approved, items still waiting for a second person). A visibility is chosen at the profile step (the
    registration) or at go-live (a next registration version, by the feed's owner), together with the approval: at
    any other step, or at go-live with no pending go-live item, it is StepNotReady, before anything is written."""
    if options.visibility is not None:
        if step not in ("profile", "go_live"):
            raise StepNotReady("the visibility is chosen at go-live")
        if step == "go_live" and not any(i.status == "pending" for i in items):
            raise StepNotReady("no go-live item is pending: the visibility is chosen with the go-live approval")
    notifier = get_notifier(ctx.settings, session=session)
    pending = [i for i in items if i.status == "pending"]
    approved: list[UUID] = []
    waiting = 0
    if step == "profile":
        approved += _approve_profile(session, sess, pending, user=user, ctx=ctx, options=options, notifier=notifier)
    elif step == "glossary":
        # 7.22 (plan D6): only what this person may decide and the agent is sure of; the rest stays pending, and its
        # column is "waiting for glossary (not loaded yet)" while the onboarding goes on
        for item in pending:
            if item.kind == "glossary_element":
                if not item.pre_accepted or "glossary_owner" not in user.roles:
                    continue
                done = decide_element(session, proposal_id=item.proposal_id, user=user, decision="approved",
                                      choice=None, note=None, notifier=notifier,
                                      stewards=_with_role(ctx, "data_steward"))
                approved.append(item.proposal_id)
                if done.mapping_proposal_id is not None:  # the requested column's mapping: its steward decides
                    show_requested_mapping(session, session_id=sess.id, result=done, user=user)
                    if "data_steward" in user.roles:
                        decide_mapping(session, proposal_id=done.mapping_proposal_id, user=user, decision="approved",
                                       choice=None, note=None)
                        approved.append(done.mapping_proposal_id)
            elif item.kind == "mapping" and "data_steward" in user.roles:
                decide_mapping(session, proposal_id=item.proposal_id, user=user, decision="approved", choice=None,
                               note=None)
                approved.append(item.proposal_id)
    elif step == "mapping":
        for item in pending:
            decide_mapping(session, proposal_id=item.proposal_id, user=user, decision="approved", choice=None, note=None)
            approved.append(item.proposal_id)
    elif step == "transformations":
        for item in pending:
            decide_transformation(session, proposal_id=item.proposal_id, user=user, decision="approved")
            approved.append(item.proposal_id)
    elif step == "rules":
        for item in pending:
            try:
                result = decide_rule(session, proposal_id=item.proposal_id, user=user, decision="approved",
                                     notifier=notifier, checkers=_with_role(ctx, "checker"))
            except (SameApproverTwice, NotAnApprover):
                if item.waiting_for is None:
                    raise
                waiting += 1  # the steward's step is in; the checker is another person
                continue
            approved.append(item.proposal_id)
            waiting += isinstance(result, RuleAwaitsChecker)
    elif step == "go_live":
        if options.visibility is not None:
            # chosen here, by the owner, after the registration: a next registration version only when it differs;
            # anyone but the registration's owner is NotAnApprover (403)
            revise_visibility(session, feed_id=sess.feed_id, user=user, visibility=options.visibility)
        for item in pending:
            decide_go_live(session, proposal_id=item.proposal_id, user=user, decision="approved", note=None,
                           back_to_step=None, today=date.today(), hypercare_days=ctx.settings.hypercare_days,
                           daily_run_utc=ctx.daily_run_utc, notifier=notifier,
                           backend_team=_with_role(ctx, "backend_team"), operations=_with_role(ctx, "operations"),
                           role_holders=lambda role: _with_role(ctx, role))
            approved.append(item.proposal_id)
    else:
        raise StepNotReady(f"{step} has nothing to approve")
    return approved, waiting


def _approve_profile(session: Session, sess: OnboardingSession, pending: list[OnboardingItem], *, user: User,
                     ctx: OnboardingContext, options: ApproveOptions, notifier) -> list[UUID]:
    """Step 03 with the outcome the agent recommended, then step 04: the operator is the owner (who must be a data
    owner), the licence is the upload source's. Registration queues no glossary run here (7.22): the next step maps
    to the existing glossary, and a glossary element is requested per column by a person."""
    catalogue = _catalogue(session, ctx.settings)
    approved: list[UUID] = []
    for item in [i for i in pending if i.kind == "profile_action"]:  # T1: the actions still standing are selected
        decide_profile_action(session, proposal_id=item.proposal_id, user=user, decision="approved")
        approved.append(item.proposal_id)
    for item in [i for i in pending if i.kind == "profile_summary"]:
        outcome = item.payload["recommended_outcome"]
        sign_off(session, feed_id=sess.feed_id, user=user, outcome=outcome,
                 gaps=item.payload.get("gaps", []) if outcome == "meets_with_gaps" else [],
                 reasons=item.payload.get("reasons", []) if outcome == "rejected" else [], catalogue=catalogue,
                 notifier=notifier, backend_team=_with_role(ctx, "backend_team"))
        approved.append(item.proposal_id)
    if FeedRepository(session).get(sess.feed_id).stage == "candidate":
        registered = register_feed(session, feed_id=sess.feed_id, user=user, owner_id=user.id,
                                   visibility=options.visibility or "open", catalogue=catalogue, identity=ctx.identity,
                                   notifier=notifier, queue_glossary=False)  # 7.22: mapping first, no run now
        if isinstance(registered, NoOwner):
            raise OwnerNotADataOwner(f"{user.id} cannot own the feed: {registered.reason.replace('_', ' ')}")
    return approved


# ---------------------------------------------------------------- edit


def _checked_payload(kind: str, payload: dict[str, Any]) -> None:
    if kind == "mapping" and payload.get("excluded"):
        raise ProposalEditRefused("A column that is left out has nothing to edit: reject the item and add it again")
    if kind == "review_summary":  # the database lets only the notes change; here, that they are notes
        notes = payload.get("notes", [])
        if not isinstance(notes, list) or not all(isinstance(n, dict) and str(n.get("text", "")).strip() for n in notes):
            raise ProposalEditRefused("The notes of a review summary are a list of notes, each with its text")
        return
    model = {"glossary_element": GlossaryProposalPayload, "mapping": MappingProposalPayload,
             "transformation": TransformationProposalPayload, "rule": RuleProposalPayload}.get(kind)
    if model is None:
        raise ProposalEditRefused(f"A {kind} proposal cannot be edited")
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        raise ProposalEditRefused(f"The edited {kind} proposal is not valid: {exc.errors()[0]['msg']}") from exc


def edit_item(session: Session, *, session_id: UUID, proposal_id: UUID, payload: dict[str, Any],
              user: User) -> Proposal:
    """The operator edits an item of the current step before it is approved: a new pending proposal on the same logged
    call (ProposalRepository.edit), event `edited` ("edited by"), and the editor's own acceptance (`pre_accepted` with
    the editor as actor). The agent's answer in the log stays as it was. ProposalEditRefused, ProposalAlreadyDecided,
    NotAnApprover, StepNotEnterable (not an item of the current step)."""
    sess = _locked(session, session_id)
    proposals = ProposalRepository(session)
    old = proposals.get(proposal_id)
    step = _item_step(session, sess, old)
    _checked_payload(old.kind, payload)
    new = proposals.edit(proposal_id, payload=payload, user=user)
    changed = sorted(k for k in set(old.payload) | set(payload) if old.payload.get(k) != payload.get(k))
    sessions = OnboardingRepository(session)
    sessions.add_event(session_id, step=step, event="edited", actor=user.id, proposal_id=new.id,
                       detail={"edited_by": user.id, "edited_from": str(old.id), "changed": changed})
    sessions.add_event(session_id, step=step, event="pre_accepted", actor=user.id, proposal_id=new.id,
                       detail={"reason": "edited by the person who will approve it"})
    state = sess.step_states[step]
    if state in ("ready", "needs_decision"):
        sessions.set_step_state(session_id, step, _settled_state(_step_items(session, sessions.get(session_id), step)))
    return new


def _item_step(session: Session, sess: OnboardingSession, proposal: Proposal) -> str:
    """The step whose item this is: the current step's. StepNotEnterable otherwise."""
    step = STEP_OF_KIND.get(proposal.kind)
    if proposal.feed_id != sess.feed_id or step is None or step != sess.current_step \
            or proposal.id not in {i.proposal_id for i in _step_items(session, sess, step)}:
        raise StepNotEnterable(f"Proposal {proposal.id} is not an item of {sess.current_step}, the step this "
                               "onboarding is on")
    return step


def reject_item(session: Session, *, session_id: UUID, proposal_id: UUID, user: User, note: str,
                ctx: OnboardingContext | None = None) -> Proposal:
    """A person rejects one item of the current step (its step's own decision, with the note)."""
    sess = _locked(session, session_id)
    proposal = ProposalRepository(session).get(proposal_id)
    step = _item_step(session, sess, proposal)
    notifier = get_notifier(ctx.settings, session=session) if ctx else None
    if proposal.kind == "glossary_element":
        decide_element(session, proposal_id=proposal_id, user=user, decision="rejected", choice=None, note=note,
                       notifier=notifier, stewards=[])
    elif proposal.kind == "mapping":
        decide_mapping(session, proposal_id=proposal_id, user=user, decision="rejected", choice=None, note=note)
    elif proposal.kind == "transformation":
        decide_transformation(session, proposal_id=proposal_id, user=user, decision="rejected", note=note)
    elif proposal.kind == "rule":
        decide_rule(session, proposal_id=proposal_id, user=user, decision="rejected", note=note, notifier=notifier)
    else:
        raise StepNotEnterable(f"A {proposal.kind} item is rejected from its own step")
    OnboardingRepository(session).add_event(session_id, step=step, event="rejected", actor=user.id,
                                            proposal_id=proposal_id, detail={"note": note})
    return ProposalRepository(session).get(proposal_id)


# ---------------------------------------------------------------- add an item by hand (phase 7.12, T7; migration 0028)

# who decides a manual item: the roles of the agent whose item it stands in for (agents/<name>.py approver_roles)
MANUAL_ROLES: dict[str, list[str]] = {"profile_action": ["backend_team"], "glossary_element": ["glossary_owner"],
                                      "mapping": ["data_steward"],
                                      "transformation": ["data_steward"], "rule": ["data_steward", "checker"]}


def _step_for_adding(session: Session, session_id: UUID, kind: str) -> tuple[OnboardingSession, str]:
    sess = _locked(session, session_id)
    step = sess.current_step
    if sess.status == "done" or STEP_OF_KIND.get(kind) != step:
        raise StepNotEnterable(f"A {kind} item is added at {STEP_OF_KIND.get(kind, 'no step')}; "
                               f"this onboarding is on {step}")
    if sess.step_states[step] not in ("ready", "needs_decision"):
        raise StepNotReady(f"{step} is {sess.step_states[step]}: add items once the step has its answer")
    return sess, step


def _profile_column(session: Session, feed_id: UUID, column: str):
    profile = FeedProfileRepository(session).latest(feed_id)
    found = next((c for c in profile.columns if c.name == column), None) if profile else None
    if found is None:
        raise ManualItemRefused(f"The file has no column {column!r}")
    return found


def _rule_from(item: ManualRule, agent_input: RuleRecommenderInput) -> ProposedRule:
    """A ProposedRule from the template the person picked (its rule key, among the offers), or the person's own."""
    if item.template is None:
        return ProposedRule(rule_key=item.rule_key, description=item.description, sql=item.sql,
                            element_keys=list(item.element_keys), template=None)
    offers = [o for e in agent_input.elements for o in e.templates] + list(agent_input.feed_templates)
    offer = next((o for o in offers if o.rule_key == item.template), None)
    if offer is None:
        raise ManualItemRefused(f"No template with the rule key {item.template!r} is offered for this feed")
    return ProposedRule(rule_key=offer.rule_key, description=offer.description, sql=offer.sql,
                        element_keys=list(offer.element_keys), template=offer.template)


def manual_rule_input(session: Session, *, session_id: UUID, item: ManualRule | None, ctx: OnboardingContext,
                      edit_of: UUID | None = None, edited: dict[str, Any] | None = None
                      ) -> tuple[RuleRecommenderInput, ProposedRule, str | None]:
    """Read only: what the back-test of a rule a person wrote needs (step 09's input), the rule, and the key of the
    pending proposal it edits. Called first, then the transaction ends, then check_manual_rule() runs with no connection
    held, then add_item() (or edit_item()) writes. ItemAlreadyPending, ManualItemRefused, and step 09's own refusals
    of the feed (FeedNotRegistered, NothingToMap)."""
    sess = OnboardingRepository(session).get(session_id)
    agent_input = rules_input(session, feed_id=sess.feed_id, catalogue=_catalogue(session, ctx.settings))
    if edit_of is not None:  # an edit of a pending rule (an agent's or a person's): its new SQL is back-tested too
        old = ProposalRepository(session).get(edit_of)
        _checked_payload("rule", edited or {})
        rule = ProposedRule(rule_key=old.payload["rule_key"], description=edited["description"], sql=edited["sql"],
                            element_keys=list(edited["element_keys"]), template=edited.get("template"))
        return agent_input, rule, old.payload["rule_key"]
    rule = _rule_from(item, agent_input)
    if any(p.payload["rule_key"] == rule.rule_key
           for p in ProposalRepository(session).list_for_feed(sess.feed_id, "rule", "pending")):
        raise ItemAlreadyPending(f"Rule {rule.rule_key} already has a pending item: edit or reject it first")
    return agent_input, rule, None


def checked_rule_payload(agent_input: RuleRecommenderInput, rule: ProposedRule, own_key: str | None, *,
                         ctx: OnboardingContext) -> dict[str, Any]:
    """No session. The rule's payload with its back-test, or ManualRuleRefused."""
    checked = check_manual_rule(agent_input, rule, own_key=own_key, connector_factory=ctx.connector,
                                rule_timeout_seconds=ctx.settings.rule_timeout_seconds, blob_store=ctx.blob_store)
    return RuleProposalPayload(rule_key=checked.rule_key, sql=checked.sql, description=checked.description,
                               element_keys=checked.element_keys, template=checked.template,
                               backtest=checked.backtest).model_dump(mode="json")


def _manual_payload(session: Session, feed_id: UUID, item, rule_payload: dict[str, Any] | None) -> dict[str, Any]:
    proposals, glossary = ProposalRepository(session), GlossaryRepository(session)
    if isinstance(item, ManualRule):
        if rule_payload is None:
            raise ManualItemRefused("A rule is back-tested before it is added: use manual_rule_input() and "
                                    "checked_rule_payload() first")
        return rule_payload
    if isinstance(item, ManualProfileAction):  # T1: an action a person will carry out themselves
        profile = FeedProfileRepository(session).latest(feed_id)
        for name in item.columns:
            _profile_column(session, feed_id, name)
        return {"profile_id": str(profile.id), "action": item.action, "columns": list(item.columns),
                "params": dict(item.params), "applied_by": "manual", "reason": item.reason}
    column = _profile_column(session, feed_id, item.source_field)
    if proposals.pending_for_column(item.kind, feed_id, item.source_field) is not None:
        raise ItemAlreadyPending(f"Column {item.source_field!r} already has a pending {item.kind} item: "
                                 "edit or reject it first")
    mappings = {m.source_field: m.element_key for m in MappingRepository(session).current_for_feed(feed_id)}
    if isinstance(item, ManualGlossary):
        exists = glossary.exists(item.option.key)
        if item.option.kind == "new_element" and exists:
            raise ManualItemRefused(f"{item.option.key} is already in the glossary: add an alias to it instead")
        if item.option.kind == "alias" and not exists:
            raise ManualItemRefused(f"{item.option.key} is not in the glossary: add it as a new element instead")
        return GlossaryProposalPayload(
            source_field=item.source_field, outcome="proposed" if item.option.kind == "new_element" else "duplicate_alias",
            options=[item.option], recommended=0, reason=item.reason, run_id=None).model_dump(mode="json")
    if isinstance(item, ManualMapping):
        if item.excluded:
            if item.source_field in mappings:
                raise ColumnAlreadyMapped(f"Column {item.source_field!r} is mapped to {mappings[item.source_field]}: "
                                          "only an unmapped column is left out")
            return {"source_field": item.source_field, "column_type": column.type, "candidates": [],
                    "recommended": None, "reason": item.reason, "excluded": True}
        if not glossary.exists(item.element_key):
            raise ManualItemRefused(f"{item.element_key} is not in the glossary")
        return MappingProposalPayload(source_field=item.source_field, column_type=column.type,
                                      candidates=[item.element_key], recommended=item.element_key,
                                      reason=item.reason).model_dump(mode="json")
    if isinstance(item, ManualTransformation):
        if item.source_field not in mappings:
            raise SourceFieldNotMapped(f"Column {item.source_field!r} has no mapping; map it before cleaning it")
        return TransformationProposalPayload(
            source_field=item.source_field, element_key=mappings[item.source_field],
            steps=[s.model_dump(mode="json") for s in item.steps], reason=item.reason,
            confidence=1.0).model_dump(mode="json")
    raise ManualItemRefused(f"A {item.kind} item cannot be added")


def add_item(session: Session, *, session_id: UUID, item, user: User,
             rule_payload: dict[str, Any] | None = None) -> Proposal:
    """A person adds an item to the current step. A manual item is a proposal like any other (rule 1): origin `manual`,
    `author`, pending until approved, and no model call, so no proposal_log row (migration 0028 refuses a log row on it).
    The author's own acceptance counts as an edit's does (`pre_accepted` by the author); with four-eyes on, the approval
    steps still need a second person. A rule needs its `rule_payload` (checked_rule_payload(): the back-test). On a go-live
    note the summary is edited, not added to: its notes are the only thing that changes (`edited` event).
    StepNotEnterable, StepNotReady, ItemAlreadyPending, ColumnAlreadyMapped, SourceFieldNotMapped, ManualItemRefused,
    ManualRuleRefused (from checked_rule_payload), NotAnApprover."""
    sess, step = _step_for_adding(session, session_id, item.kind)
    sessions, proposals = OnboardingRepository(session), ProposalRepository(session)
    if isinstance(item, ManualNote):
        return _add_note(session, sess, step, item, user)
    roles = MANUAL_ROLES[item.kind]
    if not set(roles) & set(user.roles):
        raise NotAnApprover(f"A {item.kind} item is decided by {roles}; {user.id} has {user.roles}")
    payload = _manual_payload(session, sess.feed_id, item, rule_payload)
    created = proposals.create_manual(kind=item.kind, payload=payload, required_approver_roles=roles,
                                      feed_id=sess.feed_id, author=user.id)
    sessions.add_event(session_id, step=step, event="item_added", actor=user.id, proposal_id=created.id,
                       detail={"author": user.id, "author_name": user.display_name, "kind": item.kind})
    sessions.add_event(session_id, step=step, event="pre_accepted", actor=user.id, proposal_id=created.id,
                       detail={"reason": "added by the person who will approve it"})
    sessions.set_step_state(session_id, step, _settled_state(_step_items(session, sessions.get(session_id), step)))
    return created


def _add_note(session: Session, sess: OnboardingSession, step: str, item: ManualNote, user: User) -> Proposal:
    pending = [i for i in _step_items(session, sess, step) if i.kind == "review_summary" and i.status == "pending"]
    if not pending:
        raise StepNotReady("There is no go-live summary waiting for a note")
    old = pending[0]
    note = {"author": user.id, "author_name": user.display_name, "text": item.note,
            "at": datetime.now(UTC).isoformat()}
    payload = {**old.payload, "notes": [*old.payload.get("notes", []), note]}
    new = edit_item(session, session_id=sess.id, proposal_id=old.proposal_id, payload=payload, user=user)
    return new


# ---------------------------------------------------------------- what the audit trail and the screen read


def timeline(session: Session, *, session_id: UUID) -> list[TimelineEntry]:
    """Every event, proposal (with the logged call behind it: agent, model, prompt version, latency), edit and approval
    of the onboarding's feed, in time order."""
    sess = OnboardingRepository(session).get(session_id)
    entries = [TimelineEntry(at=e.at, kind="event", actor=e.actor, step=e.step, title=e.event,
                             proposal_id=e.proposal_id, detail=e.detail)
               for e in OnboardingRepository(session).events(session_id)]
    proposals = ProposalRepository(session)
    names = {e.proposal_id: e.detail.get("author_name") for e in OnboardingRepository(session).events(session_id)
             if e.event == "item_added"}
    for kinds in STEP_KINDS.values():
        for kind in kinds:
            for p in proposals.list_for_feed(sess.feed_id, kind):
                if p.origin in ("manual", "rule"):  # no model call, so no logged call to show (0028; 0031: made by code)
                    who = "rules" if p.origin == "rule" else (names.get(p.id) or p.author)
                    facts = {"kind": p.kind, "status": p.status, "origin": p.origin, "author": p.author,
                             "author_name": who}
                    if p.edited_from is not None:
                        entries.append(TimelineEntry(
                            at=p.created_at, kind="edit", actor=p.edited_by or "unknown", step=STEP_OF_KIND[kind],
                            title=f"{kind} edited", proposal_id=p.id,
                            detail={**facts, "edited_from": str(p.edited_from), "edited_by": p.edited_by}))
                    else:
                        entries.append(TimelineEntry(at=p.created_at, kind="proposal", actor=p.author or "rules",
                                                     step=STEP_OF_KIND[kind],
                                                     title=f"{kind} proposed by rules" if p.origin == "rule"
                                                     else f"{kind} added by {who}",
                                                     proposal_id=p.id, detail=facts))
                    for a in proposals.approvals(p.id):
                        entries.append(TimelineEntry(
                            at=a.created_at, kind="approval", actor=a.decided_by, step=STEP_OF_KIND[kind],
                            title=f"{kind} {a.decision} ({a.role}, step {a.step})", proposal_id=p.id,
                            detail={"decision": a.decision, "role": a.role, "step": a.step, "note": a.note}))
                    continue
                call = session.get(ProposalLogEntry, p.proposal_log_id)
                facts = {"kind": p.kind, "status": p.status, "agent": call.agent_name, "provider": call.provider,
                         "model": call.model, "model_version": call.model_version, "prompt_id": call.prompt_id,
                         "prompt_version": call.prompt_version, "latency_ms": call.latency_ms,
                         "input_ref": call.input_ref}
                if p.edited_from is not None:
                    entries.append(TimelineEntry(
                        at=p.created_at, kind="edit", actor=p.edited_by or "unknown", step=STEP_OF_KIND[kind],
                        title=f"{kind} edited", proposal_id=p.id,
                        detail={**facts, "edited_from": str(p.edited_from), "edited_by": p.edited_by}))
                else:
                    entries.append(TimelineEntry(at=p.created_at, kind="proposal", actor=call.agent_name,
                                                 step=STEP_OF_KIND[kind], title=f"{kind} proposed", proposal_id=p.id,
                                                 detail=facts))
                for a in proposals.approvals(p.id):
                    entries.append(TimelineEntry(
                        at=a.created_at, kind="approval", actor=a.decided_by, step=STEP_OF_KIND[kind],
                        title=f"{kind} {a.decision} ({a.role}, step {a.step})", proposal_id=p.id,
                        detail={"decision": a.decision, "role": a.role, "step": a.step, "note": a.note}))
    entries += load_timeline(session, sess.feed_id)  # every scheduled load, the latest too (follow-up item 2)
    return sorted(entries, key=lambda entry: entry.at)


def _glossary_before_this_feed(session: Session, feed_id: UUID) -> list:
    """The glossary as the builder saw it: the elements (and alias versions) this feed's own approved proposals added
    are taken out, so a count recomputed after the step equals the one the agent was given."""
    glossary = GlossaryRepository(session)
    own = {p.id for p in ProposalRepository(session).list_for_feed(feed_id, "glossary_element")}
    pool = []
    for element in glossary.list_current():
        if element.proposal_id in own:
            before = [h for h in glossary.history(element.key) if h.proposal_id not in own]
            if not before:
                continue
            element = before[-1]
        pool.append(element)
    return pool


def _candidate_counts(session: Session, feed_id: UUID, step: str, items: list[OnboardingItem],
                      profile) -> dict[str, int]:
    """Per column of the step's items, how many candidate elements the agent was given. The agent's input is stored in
    the blob store, not the database, so the count is recomputed, read only, with core/steps/candidates.py over the
    same facts the step used (the profile's column, its spec, the steward's rejections for the mapping step)."""
    columns = {c.name: c for c in profile.columns}
    counts: dict[str, int] = {}
    if step == "mapping":
        glossary, mappings = GlossaryRepository(session).list_current(), MappingRepository(session)
    else:
        glossary = _glossary_before_this_feed(session, feed_id)
    for item in items:
        column = columns.get(item.column or "")
        if column is None:
            continue
        declared = profile.spec.columns.get(column.name)
        if step == "mapping":
            hint = declared if declared in profile.spec.elements else None
            rejected = mappings.rejected_elements(feed_id, column.name)
            counts[column.name] = len([x for x in candidate_list(column.name, column.type, glossary, hint=hint)
                                       if x.key not in rejected])
        else:
            also = (declared,) if declared in profile.spec.other_fields else ()
            counts[column.name] = len(candidate_list(column.name, column.type, glossary, also=also))
    return counts


def _fact(facts: list[InputFact], label: str, count: int | None, unit: str = "") -> None:
    """A fact only when it has something to say: a total of 0 is left out ("0 of 0" never appears)."""
    if count:
        facts.append(InputFact(label=label, value=f"{count}{unit}"))


def _unused_rule_templates(session: Session, feed_id: UUID, settings: Settings) -> list[dict[str, Any]]:
    """The templates a person may pick for a manual rule: the feed's offers whose rule key no stored rule, live proposal or
    rejection already has (T7). Empty when the feed has nothing to write rules for yet."""
    try:
        agent_input = rules_input(session, feed_id=feed_id, catalogue=_catalogue(session, settings))
    except (NothingToMap, FeedNotRegistered, FeedNotProfiled):
        return []
    taken = {r.rule_key for r in RuleRepository(session).current_for_feed(feed_id)}
    taken |= set(RuleRepository(session).rejected_keys(feed_id))
    taken |= {p.payload["rule_key"] for p in ProposalRepository(session).list_for_feed(feed_id, "rule")
              if p.status in ("pending", "approved")}
    offers = [o for e in agent_input.elements for o in e.templates] + list(agent_input.feed_templates)
    seen: set[str] = set()
    found = []
    for offer in offers:
        if offer.rule_key not in taken and offer.rule_key not in seen:
            seen.add(offer.rule_key)
            found.append(offer.model_dump(mode="json"))
    return found


def _step_inputs(session: Session, sess: OnboardingSession, step: str, items: list[OnboardingItem],
                 settings: Settings) -> StepInputs | None:
    """"Inputs it saw" for a step, from the database only: counts, names and flags, never a sample value."""
    profile = FeedProfileRepository(session).latest(sess.feed_id)
    if profile is None:
        return None
    found: dict[str, Any] = {"row_count": profile.row_count, "column_count": len(profile.columns),
                             "columns": [c.name for c in profile.columns]}
    facts: list[InputFact] = []
    feed_id = sess.feed_id
    if step == "profile":
        found["issues"] = profile.issues
        summary = next((i for i in items if i.kind == "profile_summary"), None)
        if summary is not None:
            keys = list(summary.payload.get("key_columns", []))
            duplicate = summary.payload.get("duplicate_key")
            found["key_columns"] = keys
            found["duplicate_key_count"] = duplicate["extra_rows"] if duplicate else (0 if keys else None)
    elif step in ("glossary", "mapping"):
        found["candidate_counts"] = _candidate_counts(session, feed_id, step, items, profile)
        _fact(facts, "Columns asked about" if step == "glossary" else "Columns proposed", len(items))
    elif step == "transformations":
        _fact(facts, "Columns cleaned", len(items))
    if step in ("mapping", "transformations", "rules", "go_live"):
        _fact(facts, "Mapped columns", len(MappingRepository(session).current_for_feed(feed_id)))
    if step == "rules":
        _fact(facts, "Rules proposed", len(items))
        found["rule_templates"] = _unused_rule_templates(session, feed_id, settings)
    if step == "go_live":
        _fact(facts, "Approved rules", len(RuleRepository(session).current_for_feed(feed_id)))
        _fact(facts, "Transformations", len(TransformationRepository(session).current_for_feed(feed_id)))
        found["completion"] = completion_report(session, feed_id).model_dump(mode="json")
        found["expected_score"] = expected_score(session, feed_id, threshold=settings.trust_release_threshold)
        test = next((x for x in LoadRepository(session).list_for_feed(feed_id) if x.purpose == "go_live_test"), None)
        if test is not None:
            rows = StandardRecordRepository(session).count(test.id)
            facts.append(InputFact(label="Test load",
                                   value=f"{test.status.replace('_', ' ')}" + (f", {rows} rows" if rows else "")))
    if step == "first_load":
        facts += first_load_facts(session, feed_id)
        found["load_summary"] = latest_load_summary(session, feed_id)
        found["load_breakdown"] = load_breakdown(session, feed_id, threshold=settings.trust_release_threshold)
    return StepInputs(**found, facts=facts)


def workspace(session: Session, *, session_id: UUID, settings: Settings | None = None,
              step: str | None = None) -> Workspace:
    """What the screen shows: the nine steps with their states, the viewed step's items (the current step unless
    `step` names another, which is read only), the job, the agent's notes for the right-hand panel, the model-call
    count and why Approve & next is disabled, if it is."""
    settings = settings or Settings(_env_file=None)
    sessions = OnboardingRepository(session)
    sess = sessions.get(session_id)
    feed = FeedRepository(session).get(sess.feed_id)
    events = sessions.events(session_id)
    requested = step
    step = sess.current_step  # Approve & next and the progress line are always about the current step
    viewed = step if requested is None else requested
    if viewed not in ONBOARDING_STEPS:
        raise StepNotEnterable(f"{viewed} is not one of the steps: {', '.join(ONBOARDING_STEPS)}")
    current_items = _step_items(session, sess, step) if step in STEP_KINDS else []
    items = current_items if viewed == step else (_step_items(session, sess, viewed) if viewed in STEP_KINDS else [])

    def why_failed(name: str) -> str | None:
        found = [e for e in events if e.step == name and e.event in ("agent_failed", "call_cap_reached")]
        return found[-1].detail.get("message") if found else None

    views = []
    for name in ONBOARDING_STEPS:
        state = sess.step_states[name]
        detail = why_failed(name) if state == "failed" else None
        if name == step and any(i.waiting_for for i in current_items):
            detail = f"Waits for {SECOND_PERSON}"
        views.append(StepView(name=name, state=state, detail=detail))

    jobs = OnboardingJobRepository(session)
    job = jobs.active_for_session(session_id) or (jobs.list_for_session(session_id) or [None])[-1]
    state = sess.step_states[step]
    agent = AGENT_NAMES.get(step, "The loader")
    if state == "agent_working":
        progress = (f"{agent} is working on {step}." if job is not None and job.status == "running"
                    else f"{step}: waiting for a worker to start {agent}.")
    elif state == "failed":
        progress = why_failed(step)
    else:
        progress = None

    notes = []
    for item in items:
        if item.origin != "agent":
            continue  # no model was called: there is no agent's note (migration 0028; 0031)
        call = session.get(ProposalLogEntry, ProposalRepository(session).get(item.proposal_id).proposal_log_id)
        notes.append(AgentNote(proposal_id=item.proposal_id, agent=call.agent_name, provider=call.provider,
                               model=call.model, model_version=call.model_version, prompt_id=call.prompt_id,
                               prompt_version=call.prompt_version, latency_ms=call.latency_ms,
                               input_ref=call.input_ref, reasoning=item.reason))

    blocking = [] if step == "glossary" else [i for i in current_items if i.needs_decision]  # D6
    if viewed != step:
        blocked = "Viewing an earlier step."  # read only: nothing is approved from another step's view
    elif sess.status == "done":
        blocked = "This onboarding is finished."
    elif state not in ("ready", "needs_decision"):
        blocked = progress or f"{step} is {state}."
    elif blocking:
        blocked = f"{len(blocking)} item(s) need a decision first."
    elif undecided := _undecided_columns(session, sess, step):
        blocked = _cannot_map_message(undecided)
    else:
        blocked = None
    key_policy = None
    if viewed == "transformations" and feed.stage == "registered":
        key_policy = effective_key_policy(session, feed.id).model_dump(mode="json")
    registration = FeedRegistrationRepository(session).current(feed.id)
    return Workspace(session=sess, feed_code=feed.code, steps=views, current_step=step, viewed_step=viewed,
                     visibility=registration.visibility if registration else None,
                     inputs=_step_inputs(session, sess, viewed, items, settings), items=items, job=job,
                     progress=progress, agent_notes=notes, key_policy=key_policy, model_calls=sess.model_calls,
                     model_call_cap=settings.max_model_calls_per_onboarding, can_approve=blocked is None,
                     blocked_by=blocked, cannot_map=cannot_map_of(session, sess))
