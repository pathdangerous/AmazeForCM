"""LoadRepository: deliveries of registered feeds, and the stages of the engine's run (step 13, phase 6). The
database guards (migration 0018, G7 to G11; 0019, G12 and G13) hold the rules; the checks here answer with a
named error first."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.load import (
    STAGES, ArrivalOutcome, Load, LoadDraft, LoadStage, LoadStageDraft, LoadStatus, Stage)
from core.repositories.errors import FeedNotFound, FeedNotRegistered, LoadInProgress, LoadNotFound
from core.repositories.tables import FeedRow, LoadStageRow
from core.repositories.tables import LoadRow as Row

_STATUS = TypeAdapter(LoadStatus)
_ENDS_ON_ARRIVAL = {"resent_quarantined": "quarantined", "breaking_held": "held"}  # arrival outcome -> status


class LoadRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, *, feed_id: UUID, **fields: Any) -> Load:
        """A received load. One per feed at a time (uq_loads_one_received_per_feed): another gives
        LoadInProgress."""
        draft = LoadDraft(**fields)
        feed = self._session.get(FeedRow, feed_id)
        if feed is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        if feed.stage != "registered":
            raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}; loads arrive only for registered feeds")
        if self._session.scalar(select(Row.id).where(Row.feed_id == feed_id, Row.status == "received")) is not None:
            raise LoadInProgress(f"Feed {feed.code!r} has a load in progress")
        values = draft.model_dump(exclude_none=True)  # received_at defaults to the moment of insert
        row = Row(feed_id=feed_id, status="received", **values)
        self._session.add(row)
        self._session.flush()
        return Load.model_validate(row)

    def get(self, load_id: UUID) -> Load:
        return Load.model_validate(self._row(load_id))

    def lock(self, load_id: UUID) -> Load:
        """The load, with its row locked (FOR NO KEY UPDATE) until the transaction ends. Last in the lock
        order (phase 6 plan, assumption 36): after the feed row."""
        return Load.model_validate(self._row(load_id, lock=True))

    def received_for_feed(self, feed_id: UUID) -> Load | None:
        """The feed's received load, if any (at most one: uq_loads_one_received_per_feed)."""
        row = self._session.scalars(select(Row).where(Row.feed_id == feed_id, Row.status == "received")).one_or_none()
        return None if row is None else Load.model_validate(row)

    def list_for_feed(self, feed_id: UUID) -> list[Load]:
        query = select(Row).where(Row.feed_id == feed_id).order_by(Row.received_at.desc(), Row.created_at.desc())
        return [Load.model_validate(r) for r in self._session.scalars(query)]

    def record_stages(self, load_id: UUID, stages: Sequence[LoadStageDraft]) -> list[LoadStage]:
        """The stage rows of a received load, in the engine's order (trg_load_stages_b_order)."""
        self._row(load_id)  # LoadNotFound
        rows = [LoadStageRow(load_id=load_id, **stage.model_dump()) for stage in stages]
        self._session.add_all(rows)
        self._session.flush()
        return [LoadStage.model_validate(r) for r in rows]

    def stages(self, load_id: UUID) -> list[LoadStage]:
        """In the engine's order."""
        rows = self._session.scalars(select(LoadStageRow).where(LoadStageRow.load_id == load_id))
        return [LoadStage.model_validate(r) for r in sorted(rows, key=lambda r: STAGES.index(r.stage))]

    def finish(self, load_id: UUID, *, status: Literal["loaded", "failed_rolled_back"],
               failed_stage: Stage | None = None, failure: str | None = None) -> Load:
        """Move a received load to loaded (its four stages completed) or failed_rolled_back (one failed stage
        and no standard record): trg_loads_a_status checks the proof."""
        row = self._row(load_id)
        row.status, row.finished_at = status, datetime.now(UTC)
        row.failed_stage, row.failure = failed_stage, failure
        self._session.flush()
        return Load.model_validate(row)

    def set_status(self, load_id: UUID, status: LoadStatus) -> Load:
        """Any other move the database allows: a certified scheduled load to held or released (step 15)."""
        status = _STATUS.validate_python(status)
        row = self._row(load_id)
        row.status = status
        self._session.flush()
        return Load.model_validate(row)

    def record_arrival(self, load_id: UUID, *, outcome: ArrivalOutcome, content_sha256: str | None,
                       detail: dict[str, Any], profile_id: UUID | None = None) -> Load:
        """Step 12's write on a received scheduled load: the four arrival fields, once (G12). A resent file
        ends the load as quarantined and a breaking one as held, in the same write with a finish time and no
        stage (G13); the other two outcomes leave it received for the engine."""
        row = self._row(load_id)
        row.content_sha256, row.arrival_outcome = content_sha256, outcome
        row.arrival_detail, row.arrival_profile_id = detail, profile_id
        if outcome in _ENDS_ON_ARRIVAL:
            row.status, row.finished_at = _ENDS_ON_ARRIVAL[outcome], datetime.now(UTC)
        self._session.flush()
        return Load.model_validate(row)

    def abandon(self, load_id: UUID, failure: str) -> Load:
        """A load left received by a dead process: no stage, no record, the failure and the finish time (G13)."""
        row = self._row(load_id)
        row.status, row.finished_at, row.failure = "abandoned", datetime.now(UTC), failure
        self._session.flush()
        return Load.model_validate(row)

    def scheduled_hashes(self, feed_id: UUID) -> dict[str, UUID]:
        """The SHA-256 of the feed's scheduled loads that were loaded, released or held, with the oldest load that
        had it. A failed_rolled_back, abandoned or quarantined load is not compared (phase 7 assumption 12): after
        a new go-live the same file must load."""
        query = (select(Row.content_sha256, Row.id)
                 .where(Row.feed_id == feed_id, Row.purpose == "scheduled", Row.content_sha256.is_not(None),
                        Row.status.in_(("loaded", "released", "held")))
                 .order_by(Row.received_at.desc(), Row.created_at.desc()))
        return {sha: load_id for sha, load_id in self._session.execute(query)}  # the oldest is read last, so it wins

    def fully_loaded_scheduled(self, feed_id: UUID, limit: int) -> list[Load]:
        """The feed's newest scheduled loads that completed all four stages (loaded, then held or released)."""
        query = (select(Row)
                 .where(Row.feed_id == feed_id, Row.purpose == "scheduled",
                        Row.status.in_(("loaded", "held", "released")))
                 .order_by(Row.received_at.desc(), Row.created_at.desc()).limit(limit))
        return [Load.model_validate(r) for r in self._session.scalars(query)]

    def _row(self, load_id: UUID, *, lock: bool = False) -> Row:
        query = select(Row).where(Row.id == load_id)
        if lock:
            query = query.with_for_update(key_share=True).execution_options(populate_existing=True)
        row = self._session.scalars(query).one_or_none()
        if row is None:
            raise LoadNotFound(f"No load with id {load_id}")
        return row
