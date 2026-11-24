"""OnboardingRepository and OnboardingJobRepository (phase 7.8 plan, assumptions 31 to 33 and 38; migration 0024).

- A session is workflow state: lock() it before a change that others may make at the same time. It sits in the lock
  order after the proposal and requirement rows of the step it changes (the orchestration, task 5.3, takes the locks).
- The job queue is glossary_runs' pattern: queue() returns the session's active job when it has one; claim() takes the
  oldest queued job, or a running one whose lease passed (sent back to queued first, as the database only allows that
  way), FOR UPDATE SKIP LOCKED, and a job that was taken MAX_JOB_ATTEMPTS times fails instead; finish() and fail() name
  the attempt, so an answer that arrives after the lease was taken again is refused (JobSuperseded).
Repositories flush but never commit; the caller owns the transaction.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.domain.onboarding import (
    ACTIVE_JOB_STATUSES,
    EVENT_NAMES,
    MAX_JOB_ATTEMPTS,
    ONBOARDING_STEPS,
    RUN_MODES,
    STEP_STATES,
    OnboardingEvent,
    OnboardingJob,
    OnboardingSession,
    SessionStatus,
    fresh_step_states,
)
from core.repositories.errors import JobSuperseded, OnboardingAlreadyExists, OnboardingNotFound
from core.repositories.tables import OnboardingEventRow, OnboardingJobRow, OnboardingSessionRow


def _step(step: str) -> str:
    if step not in ONBOARDING_STEPS:
        raise ValueError(f"{step!r} is not an onboarding step")
    return step


class OnboardingRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, *, feed_id: UUID, created_by: str) -> OnboardingSession:
        """The feed's session, made when its upload is confirmed: upload is done, the current step is profile."""
        if self.get_by_feed(feed_id) is not None:
            raise OnboardingAlreadyExists(f"Feed {feed_id} already has an onboarding session")
        row = OnboardingSessionRow(feed_id=feed_id, current_step="profile", step_states=fresh_step_states(),
                                   created_by=created_by)
        self._session.add(row)
        self._session.flush()
        self._session.refresh(row)
        return OnboardingSession.model_validate(row)

    def get(self, session_id: UUID) -> OnboardingSession:
        return OnboardingSession.model_validate(self._row(session_id))

    def get_by_feed(self, feed_id: UUID) -> OnboardingSession | None:
        row = self._session.scalars(select(OnboardingSessionRow).where(OnboardingSessionRow.feed_id == feed_id)).one_or_none()
        return None if row is None else OnboardingSession.model_validate(row)

    def lock(self, session_id: UUID) -> OnboardingSession:
        """The session row, locked until the transaction ends and read fresh."""
        return OnboardingSession.model_validate(self._row(session_id, lock=True))

    def list(self, status: SessionStatus | None = None) -> list[OnboardingSession]:
        query = select(OnboardingSessionRow).order_by(OnboardingSessionRow.created_at, OnboardingSessionRow.id)
        if status is not None:
            query = query.where(OnboardingSessionRow.status == status)
        return [OnboardingSession.model_validate(r) for r in self._session.scalars(query)]

    def set_step_state(self, session_id: UUID, step: str, state: str) -> OnboardingSession:
        if state not in STEP_STATES:
            raise ValueError(f"{state!r} is not a step state")
        row = self._row(session_id)
        row.step_states = {**row.step_states, _step(step): state}
        return self._touched(row)

    def move_to(self, session_id: UUID, step: str) -> OnboardingSession:
        row = self._row(session_id)
        row.current_step = _step(step)
        return self._touched(row)

    def set_run_mode(self, session_id: UUID, run_mode: str) -> OnboardingSession:
        """step -> chain only (0032 refuses the way back)."""
        if run_mode not in RUN_MODES:
            raise ValueError(f"{run_mode!r} is not a run mode")
        row = self._row(session_id)
        row.run_mode = run_mode
        return self._touched(row)

    def finish(self, session_id: UUID) -> OnboardingSession:
        row = self._row(session_id)
        row.status = "done"
        return self._touched(row)

    def bump_model_calls(self, session_id: UUID, by: int = 1) -> int:
        """Count model calls of this onboarding (MAX_MODEL_CALLS_PER_ONBOARDING); the new total. Atomic."""
        if by < 0:
            raise ValueError("model calls only count up")
        total = self._session.scalar(
            update(OnboardingSessionRow).where(OnboardingSessionRow.id == session_id)
            .values(model_calls=OnboardingSessionRow.model_calls + by, updated_at=func.clock_timestamp())
            .returning(OnboardingSessionRow.model_calls).execution_options(synchronize_session=False))
        if total is None:
            raise OnboardingNotFound(f"No onboarding session with id {session_id}")
        return total

    def add_event(self, session_id: UUID, *, step: str, event: str, actor: str, proposal_id: UUID | None = None,
                  detail: dict[str, Any] | None = None) -> OnboardingEvent:
        if event not in EVENT_NAMES:
            raise ValueError(f"{event!r} is not an onboarding event")
        row = OnboardingEventRow(session_id=session_id, step=_step(step), event=event, actor=actor,
                                 proposal_id=proposal_id, detail=detail or {})
        self._session.add(row)
        self._session.flush()
        self._session.refresh(row)
        return OnboardingEvent.model_validate(row)

    def events(self, session_id: UUID) -> list[OnboardingEvent]:
        """The session's events, in the order they happened."""
        rows = self._session.scalars(select(OnboardingEventRow).where(OnboardingEventRow.session_id == session_id)
                                     .order_by(OnboardingEventRow.at, OnboardingEventRow.id))
        return [OnboardingEvent.model_validate(r) for r in rows]

    # ---- helpers

    def _row(self, session_id: UUID, *, lock: bool = False) -> OnboardingSessionRow:
        query = select(OnboardingSessionRow).where(OnboardingSessionRow.id == session_id)
        if lock:
            query = query.with_for_update()
        row = self._session.scalars(query.execution_options(populate_existing=True)).one_or_none()
        if row is None:
            raise OnboardingNotFound(f"No onboarding session with id {session_id}")
        return row

    def _touched(self, row: OnboardingSessionRow) -> OnboardingSession:
        row.updated_at = func.clock_timestamp()
        self._session.flush()
        self._session.refresh(row)
        return OnboardingSession.model_validate(row)


class OnboardingJobRepository:
    def __init__(self, session: Session):
        self._session = session

    def queue(self, *, session_id: UUID, step: str) -> tuple[OnboardingJob, bool]:
        """The session's active job and whether this call created it. A session has one queued or running job: asking
        again returns that one (look at its step). Two requests at once give one row, never a unique violation."""
        inserted = self._session.execute(
            insert(OnboardingJobRow).values(session_id=session_id, step=_step(step))
            .on_conflict_do_nothing(index_elements=["session_id"],
                                    index_where=OnboardingJobRow.status.in_(ACTIVE_JOB_STATUSES))
            .returning(OnboardingJobRow.id)
        ).scalar_one_or_none()
        if inserted is not None:
            return self.get(inserted), True
        active = self.active_for_session(session_id)
        if active is None:  # it finished between the two statements: queue a new one
            return self.queue(session_id=session_id, step=step)
        return active, False

    def get(self, job_id: UUID) -> OnboardingJob:
        row = self._session.get(OnboardingJobRow, job_id, populate_existing=True)
        if row is None:
            raise LookupError(f"No onboarding job with id {job_id}")
        return OnboardingJob.model_validate(row)

    def active_for_session(self, session_id: UUID) -> OnboardingJob | None:
        row = self._session.scalars(
            select(OnboardingJobRow).where(OnboardingJobRow.session_id == session_id,
                                           OnboardingJobRow.status.in_(ACTIVE_JOB_STATUSES))
            .execution_options(populate_existing=True)).one_or_none()
        return None if row is None else OnboardingJob.model_validate(row)

    def list_for_session(self, session_id: UUID) -> list[OnboardingJob]:
        rows = self._session.scalars(select(OnboardingJobRow).where(OnboardingJobRow.session_id == session_id)
                                     .order_by(OnboardingJobRow.created_at, OnboardingJobRow.id))
        return [OnboardingJob.model_validate(r) for r in rows]

    def seconds_to_live_lease(self) -> float | None:
        """Seconds until the earliest lease of a running job passes; None when no running job has a lease that has
        not passed (task 5.4: the worker asks for one more kick then, as the glossary worker does)."""
        seconds = self._session.scalar(
            select(func.min(func.extract("epoch", OnboardingJobRow.lease_until - func.clock_timestamp())))
            .where(OnboardingJobRow.status == "running", OnboardingJobRow.lease_until >= func.clock_timestamp()))
        return None if seconds is None else max(float(seconds), 0.0)

    def claim(self, *, lease_seconds: int) -> OnboardingJob | None:
        """Take one job for this worker, or None. A running job whose lease passed goes back to queued (the only way
        the database lets it move) and is taken again on the next attempt; one that had MAX_JOB_ATTEMPTS fails."""
        while True:
            row = self._session.scalars(
                select(OnboardingJobRow)
                .where((OnboardingJobRow.status == "queued")
                       | ((OnboardingJobRow.status == "running") & (OnboardingJobRow.lease_until < func.clock_timestamp())))
                .order_by(OnboardingJobRow.created_at, OnboardingJobRow.id).limit(1)
                .with_for_update(skip_locked=True).execution_options(populate_existing=True)
            ).one_or_none()
            if row is None:
                return None
            if row.status == "running":
                if row.attempt >= MAX_JOB_ATTEMPTS:
                    row.status, row.error, row.finished_at = ("failed", f"gave up after {MAX_JOB_ATTEMPTS} attempts",
                                                              func.clock_timestamp())
                    self._session.flush()
                    continue
                row.status = "queued"
                self._session.flush()
            row.status, row.attempt = "running", row.attempt + 1
            row.started_at = func.clock_timestamp()
            row.lease_until = func.clock_timestamp() + text(f"interval '{int(lease_seconds)} seconds'")
            row.error = None
            self._session.flush()
            return self.get(row.id)

    def finish(self, job_id: UUID, *, attempt: int) -> OnboardingJob:
        row = self._running(job_id, attempt)
        row.status, row.finished_at = "done", func.clock_timestamp()
        self._session.flush()
        return self.get(job_id)

    def fail(self, job_id: UUID, *, attempt: int, error: str) -> OnboardingJob:
        row = self._running(job_id, attempt)
        row.status, row.error, row.finished_at = "failed", error, func.clock_timestamp()
        self._session.flush()
        return self.get(job_id)

    def discard(self, job_id: UUID) -> OnboardingJob:
        """End a queued or running job (the step was left or sent back); a finished job stays as it is."""
        row = self._session.scalars(select(OnboardingJobRow).where(OnboardingJobRow.id == job_id).with_for_update()
                                    .execution_options(populate_existing=True)).one_or_none()
        if row is None:
            raise LookupError(f"No onboarding job with id {job_id}")
        if row.status in ACTIVE_JOB_STATUSES:
            row.status, row.finished_at = "discarded", func.clock_timestamp()
            self._session.flush()
        return self.get(job_id)

    # ---- helpers

    def _running(self, job_id: UUID, attempt: int) -> OnboardingJobRow:
        row = self._session.scalars(select(OnboardingJobRow).where(OnboardingJobRow.id == job_id).with_for_update()
                                    .execution_options(populate_existing=True)).one_or_none()
        if row is None:
            raise LookupError(f"No onboarding job with id {job_id}")
        if row.status != "running" or row.attempt != attempt:
            raise JobSuperseded(f"Onboarding job {job_id} is {row.status} on attempt {row.attempt}, not running on {attempt}")
        return row
