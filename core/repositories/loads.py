"""LoadRepository: deliveries of registered feeds, and the stages of the engine's run (step 13, phase 6). The
database guards (migration 0018, G7 to G11) hold the rules; the checks here answer with a named error first."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.load import STAGES, Load, LoadDraft, LoadStage, LoadStageDraft, LoadStatus, Stage
from core.repositories.errors import FeedNotFound, FeedNotRegistered, LoadInProgress, LoadNotFound
from core.repositories.tables import FeedRow, LoadStageRow
from core.repositories.tables import LoadRow as Row

_STATUS = TypeAdapter(LoadStatus)


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
        """Any other move the database allows: a loaded load to held or released (certification, step 15)."""
        status = _STATUS.validate_python(status)
        row = self._row(load_id)
        row.status = status
        self._session.flush()
        return Load.model_validate(row)

    def _row(self, load_id: UUID, *, lock: bool = False) -> Row:
        query = select(Row).where(Row.id == load_id)
        if lock:
            query = query.with_for_update(key_share=True).execution_options(populate_existing=True)
        row = self._session.scalars(query).one_or_none()
        if row is None:
            raise LoadNotFound(f"No load with id {load_id}")
        return row
