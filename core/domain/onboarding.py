"""The onboarding workspace's state (phase 7.8 plan, assumptions 31 to 33; migration 0024).

One session per feed walks the nine steps in order. The session is workflow state, not configuration: its row
changes, but every change is an event (onboarding_events, append-only), and the approvals themselves stay in the
proposal tables. A background job (onboarding_jobs) runs a step's agent; it is copied from glossary_runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from core.domain.profiling import ProfileIssue

StepName = Literal["upload", "profile", "glossary", "mapping", "transformations", "rules", "go_live", "first_load",
                   "product_ready"]
ONBOARDING_STEPS: tuple[str, ...] = get_args(StepName)  # in order

StepState = Literal["waiting", "agent_working", "needs_decision", "ready", "done", "stale", "failed"]
STEP_STATES: tuple[str, ...] = get_args(StepState)

SessionStatus = Literal["active", "done"]
SESSION_STATUSES: tuple[str, ...] = get_args(SessionStatus)

EventName = Literal["entered", "agent_queued", "agent_started", "agent_done", "agent_failed", "pre_accepted",
                    "edited", "approved", "rejected", "back", "stale", "done", "call_cap_reached"]
EVENT_NAMES: tuple[str, ...] = get_args(EventName)

JobStatus = Literal["queued", "running", "done", "failed", "discarded"]
JOB_STATUSES: tuple[str, ...] = get_args(JobStatus)
ACTIVE_JOB_STATUSES: tuple[str, ...] = ("queued", "running")

MAX_JOB_ATTEMPTS = 3  # a job whose lease passed a third time fails ("gave up") instead of running again


def fresh_step_states() -> dict[str, str]:
    """A session is made when the upload is confirmed: upload is done, every other step waits."""
    return {step: ("done" if step == "upload" else "waiting") for step in ONBOARDING_STEPS}


class OnboardingSession(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    feed_id: UUID
    current_step: StepName
    step_states: dict[str, StepState]
    status: SessionStatus
    created_by: str
    created_at: datetime
    updated_at: datetime
    model_calls: int


class OnboardingEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    session_id: UUID
    step: StepName
    event: EventName
    actor: str  # a user id, or "system"
    at: datetime
    proposal_id: UUID | None
    detail: dict[str, Any]


class OnboardingJob(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    session_id: UUID
    step: StepName
    status: JobStatus
    lease_until: datetime | None
    attempt: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


# ---------------------------------------------------------------- what the orchestration returns (task 5.3)


class ApproveOptions(BaseModel):
    """What the operator may choose when Approve & next is pressed. visibility is chosen at go-live (task 7.3a): given
    there and different from the registration's, a next registration version is written first. The profile step
    registers the feed with it, or "open" when none is given."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    visibility: Literal["open", "restricted"] | None = None


class StepAdvance(BaseModel):
    """What Approve & next did: moved on, or waited (rules with four-eyes on wait for a second person)."""

    model_config = ConfigDict(frozen=True)

    moved: bool
    current_step: StepName
    approved: list[UUID]  # the proposals this call approved (a step of them, when a second person is still to come)
    waiting_for: str | None = None


class JobRun(BaseModel):
    """What the worker did with one job: done, failed, discarded (the call cap) or superseded (its lease was taken)."""

    model_config = ConfigDict(frozen=True)

    job_id: UUID
    session_id: UUID
    step: StepName
    status: Literal["done", "failed", "discarded", "superseded"]
    detail: str | None = None
    model_calls: int = 0


class OnboardingItem(BaseModel):
    """One proposal of the current step, as the workspace shows it."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    kind: str
    column: str | None  # the source column, or the rule key
    summary: str
    status: str  # pending, approved or rejected (a superseded proposal is shown as the edit that replaced it)
    confidence: float | None
    pre_accepted: bool
    needs_decision: bool
    waiting_for: str | None  # "a second person" once one approval step is in and the next is for someone else
    edited: bool
    edited_by: str | None
    edited_from: UUID | None
    reason: str | None
    payload: dict[str, Any]
    decided_by: str | None = None  # the user id of the latest approval decision on this proposal; None while pending
    decided_at: datetime | None = None


class AgentNote(BaseModel):
    """"What the agent did and why", for the right-hand panel: the logged call behind a proposal."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    agent: str
    provider: str
    model: str
    model_version: str
    prompt_id: str
    prompt_version: str
    latency_ms: int
    input_ref: str
    reasoning: str | None


class StepView(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: StepName
    state: StepState
    detail: str | None = None  # why it failed, or what it waits for


class InputFact(BaseModel):
    """One more thing a step's agent saw, as a label and a value ("Test load", "loaded, 70 rows")."""

    model_config = ConfigDict(frozen=True)

    label: str
    value: str


class RuleFlags(BaseModel):
    """One rule's result on one load: rows flagged of total, and up to five of the flagged row numbers."""

    model_config = ConfigDict(frozen=True)

    rule_key: str
    flagged_rows: int
    total_rows: int
    sample_rows: list[int]  # file row numbers, ascending, at most 5


class LoadBreakdown(BaseModel):
    """What a scheduled load did, computed from its stored records and certification (no model call): per rule, the
    trust score next to the release threshold, and what the operator can do next."""

    model_config = ConfigDict(frozen=True)

    load_id: UUID
    status: str
    rows: int
    flagged_rows: int
    trust_score: str | None  # None when no record was loaded (quarantined, failed)
    threshold: str
    below_threshold: bool
    rules: list[RuleFlags]
    next_action: str


class ExpectedScore(BaseModel):
    """The trust score the go-live test load predicts for the first load, computed from its stored records."""

    model_config = ConfigDict(frozen=True)

    rows: int
    flagged_rows: int
    trust_score: str
    threshold: str
    below_threshold: bool


class StepInputs(BaseModel):
    """"Inputs it saw" for the viewed step, from the database only: counts, names and flags; never a sample value."""

    model_config = ConfigDict(frozen=True)

    row_count: int | None = None
    column_count: int | None = None
    columns: list[str] = []  # the latest profile's column names
    issues: list[ProfileIssue] = []  # profile step: the latest profile's issues
    key_columns: list[str] = []  # profile step: the proposed key
    duplicate_key_count: int | None = None  # profile step: rows that repeat the proposed key
    candidate_counts: dict[str, int] = {}  # glossary and mapping steps: candidate elements per column
    facts: list[InputFact] = []
    load_summary: str | None = None  # first load: one sentence about the latest scheduled load (same rows as the facts)
    load_breakdown: LoadBreakdown | None = None  # first load: per rule, the score and the next action
    expected_score: ExpectedScore | None = None  # go-live: the score the test load predicts (computed)


class Workspace(BaseModel):
    model_config = ConfigDict(frozen=True)

    session: OnboardingSession
    feed_code: str
    steps: list[StepView]
    current_step: StepName
    viewed_step: StepName  # the step whose items, notes and inputs these are (the current one unless asked for)
    visibility: Literal["open", "restricted"] | None  # the latest registration's; None before registration
    inputs: StepInputs | None
    items: list[OnboardingItem]
    job: OnboardingJob | None
    progress: str | None
    agent_notes: list[AgentNote]
    key_policy: dict[str, Any] | None  # the effective duplicate-key policy, in the transformations step
    model_calls: int
    model_call_cap: int
    can_approve: bool
    blocked_by: str | None  # why Approve & next is disabled


class TimelineEntry(BaseModel):
    """One line of the audit trail: an event, a proposal (with its logged call), an edit or an approval."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    kind: Literal["event", "proposal", "edit", "approval"]
    actor: str  # a user id, "system", or the agent's name for a proposal
    step: str | None
    title: str
    proposal_id: UUID | None
    detail: dict[str, Any]
