"""FeedScheduleRepository: a feed's schedule versions (phase 7.21, migration 0039). Append-only; the database checks that the
version is the previous plus 1. Repositories flush but never commit; the caller owns the transaction. The step locks the feed
row first, so two changes of one feed wait for each other."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.schedule import FeedSchedule, ScheduleInput
from core.repositories.errors import FeedNotFound
from core.repositories.tables import FeedRow, FeedScheduleRow as Row


class FeedScheduleRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_version(self, *, feed_id: UUID, schedule: ScheduleInput, decided_by: str) -> FeedSchedule:
        if self._session.get(FeedRow, feed_id) is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        latest = self.current(feed_id)
        row = Row(feed_id=feed_id, version=(latest.version if latest else 0) + 1, decided_by=decided_by,
                  **schedule.model_dump())
        self._session.add(row)
        self._session.flush()
        self._session.refresh(row)  # created_at is the database's clock
        return FeedSchedule.model_validate(row)

    def current(self, feed_id: UUID) -> FeedSchedule | None:
        row = self._session.scalars(select(Row).where(Row.feed_id == feed_id).order_by(Row.version.desc()).limit(1)).first()
        return None if row is None else FeedSchedule.model_validate(row)

    def list_for_feed(self, feed_id: UUID) -> list[FeedSchedule]:
        """Every version of the feed, oldest first."""
        rows = self._session.scalars(select(Row).where(Row.feed_id == feed_id).order_by(Row.version))
        return [FeedSchedule.model_validate(r) for r in rows]
