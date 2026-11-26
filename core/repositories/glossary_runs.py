"""GlossaryRunRepository: step 05's runs (phase 5 plan, assumptions 10 to 12 and 19; migration 0016).

- queue(): one queued or running run per feed. Asking again while one is active returns it (a sent-back
  column joins a queued run's columns). Two requests at once give one row (ON CONFLICT on the partial
  unique index), never a unique violation.
- claim(): the oldest queued run, or a running one whose lease has passed, FOR UPDATE SKIP LOCKED: set
  running, attempt + 1, a new lease. A run that already had MAX_ATTEMPTS fails instead ("gave up").
  The caller commits at once; no lock is held afterwards.
- lock() is the run row's lock, after the feed row in the lock order (assumption 49).
Repositories flush but never commit; the caller owns the transaction.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.domain.mapping import MAX_ATTEMPTS, GlossaryRun, GlossaryRunReason
from core.repositories.errors import GlossaryRunNotFound, RunSuperseded
from core.repositories.tables import GlossaryRunRow as Row

ACTIVE = ("queued", "running")


class GlossaryRunRepository:
    def __init__(self, session: Session):
        self._session = session

    def queue(self, *, feed_id: UUID, profile_id: UUID, reason: GlossaryRunReason, requested_by: str,
              columns: Iterable[str] = ()) -> tuple[GlossaryRun, bool]:
        """The feed's active run and whether this call created it."""
        wanted = list(dict.fromkeys(columns))
        inserted = self._session.execute(
            insert(Row).values(feed_id=feed_id, profile_id=profile_id, reason=reason, requested_by=requested_by,
                               columns=wanted)
            .on_conflict_do_nothing(index_elements=["feed_id"], index_where=Row.status.in_(ACTIVE))
            .returning(Row.id)
        ).scalar_one_or_none()
        if inserted is not None:
            return self.get(inserted), True
        active = self._active_row(lock=True)(feed_id)
        if active is None:  # the active run finished between the two statements: queue a new one
            return self.queue(feed_id=feed_id, profile_id=profile_id, reason=reason, requested_by=requested_by,
                              columns=wanted)
        missing = [c for c in wanted if c not in active.columns]
        if missing and active.status == "queued":
            active.columns = [*active.columns, *missing]
            self._session.flush()
        return GlossaryRun.model_validate(active), False

    def get(self, run_id: UUID) -> GlossaryRun:
        row = self._session.get(Row, run_id, populate_existing=True)
        if row is None:
            raise GlossaryRunNotFound(f"No glossary run with id {run_id}")
        return GlossaryRun.model_validate(row)

    def find(self, run_id: UUID) -> GlossaryRun | None:
        row = self._session.get(Row, run_id)
        return None if row is None else GlossaryRun.model_validate(row)

    def active(self, feed_id: UUID) -> GlossaryRun | None:
        row = self._active_row(lock=False)(feed_id)
        return None if row is None else GlossaryRun.model_validate(row)

    def latest(self, feed_id: UUID) -> GlossaryRun | None:
        row = self._session.scalars(select(Row).where(Row.feed_id == feed_id)
                                    .order_by(Row.created_at.desc(), Row.id).limit(1)).one_or_none()
        return None if row is None else GlossaryRun.model_validate(row)

    def list_for_feed(self, feed_id: UUID) -> list[GlossaryRun]:
        rows = self._session.scalars(select(Row).where(Row.feed_id == feed_id).order_by(Row.created_at, Row.id))
        return [GlossaryRun.model_validate(r) for r in rows]

    def list_active(self) -> list[GlossaryRun]:
        """Every queued or running run, oldest first (the banner)."""
        rows = self._session.scalars(select(Row).where(Row.status.in_(ACTIVE)).order_by(Row.created_at, Row.id))
        return [GlossaryRun.model_validate(r) for r in rows]

    def recent_finished(self, limit: int = 5) -> list[GlossaryRun]:
        """The last finished runs (done or failed), newest first (the banner)."""
        rows = self._session.scalars(select(Row).where(Row.status.not_in(ACTIVE))
                                     .order_by(Row.finished_at.desc(), Row.id).limit(limit))
        return [GlossaryRun.model_validate(r) for r in rows]

    def claim(self, *, lease_seconds: int) -> GlossaryRun | None:
        """Take one run for this worker, or None. Runs that ran out of attempts are failed on the way."""
        while True:
            row = self._session.scalars(
                select(Row)
                .where((Row.status == "queued") | ((Row.status == "running") & (Row.lease_until < func.clock_timestamp())))
                .order_by(Row.created_at, Row.id).limit(1)
                .with_for_update(skip_locked=True).execution_options(populate_existing=True)
            ).one_or_none()
            if row is None:
                return None
            if row.status == "running" and row.attempt >= MAX_ATTEMPTS:
                self._finish_failed(row, f"gave up after {MAX_ATTEMPTS} attempts")
                continue
            row.status, row.attempt = "running", row.attempt + 1
            row.started_at = func.clock_timestamp()
            row.lease_until = func.clock_timestamp() + text(f"interval '{int(lease_seconds)} seconds'")
            self._session.flush()
            return self.get(row.id)

    def seconds_to_live_lease(self) -> float | None:
        """Seconds until the earliest lease of a running run passes; None when no running run has a lease
        that has not passed (a run whose lease has passed is taken by the next claim()). A run left running
        by a worker that died is claimed only after its lease, so the worker asks for one more kick then."""
        seconds = self._session.scalar(
            select(func.min(func.extract("epoch", Row.lease_until - func.clock_timestamp())))
            .where(Row.status == "running", Row.lease_until >= func.clock_timestamp()))
        return None if seconds is None else max(float(seconds), 0.0)

    def lock(self, run_id: UUID, *, attempt: int) -> GlossaryRun:
        """The run row, locked; RunSuperseded unless it is still running on this attempt."""
        row = self._session.scalars(select(Row).where(Row.id == run_id).with_for_update()
                                    .execution_options(populate_existing=True)).one_or_none()
        if row is None:
            raise GlossaryRunNotFound(f"No glossary run with id {run_id}")
        if row.status != "running" or row.attempt != attempt:
            raise RunSuperseded(f"Glossary run {run_id} is {row.status} on attempt {row.attempt}, not running on {attempt}")
        return GlossaryRun.model_validate(row)

    def finish(self, run_id: UUID, *, results: dict[str, Any], proposal_log_id: UUID | None) -> GlossaryRun:
        row = self._session.get(Row, run_id)
        row.status, row.results, row.proposal_log_id = "done", results, proposal_log_id
        row.finished_at = func.clock_timestamp()
        self._session.flush()
        return self.get(run_id)

    def fail(self, run_id: UUID, *, error: str) -> GlossaryRun:
        row = self._session.get(Row, run_id)
        self._finish_failed(row, error)
        return self.get(run_id)

    # ---- helpers

    def _finish_failed(self, row: Row, error: str) -> None:
        row.status, row.error, row.finished_at = "failed", error, func.clock_timestamp()
        self._session.flush()

    def _active_row(self, *, lock: bool):
        def find(feed_id: UUID) -> Row | None:
            query = select(Row).where(Row.feed_id == feed_id, Row.status.in_(ACTIVE))
            if lock:
                query = query.with_for_update().execution_options(populate_existing=True)
            return self._session.scalars(query).one_or_none()

        return find
