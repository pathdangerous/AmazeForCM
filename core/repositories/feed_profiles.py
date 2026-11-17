"""FeedProfileRepository: step 02's profiles, versioned per feed; the highest version is current.

Insert only (feed_profiles is append-only). The caller locks the feed row first, so two profiles of one
feed take versions one after the other; the database checks the version too (0014). Flushes, never commits.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.domain.profiling import FeedProfile, FeedProfileDraft
from core.repositories.errors import FeedNotFound
from core.repositories.tables import FeedProfileRow as Row
from core.repositories.tables import FeedRow


def next_version(session: Session, table, feed_id: UUID) -> int:
    """The feed's highest version in this table plus one; shared by the three phase 4 repositories."""
    if session.get(FeedRow, feed_id) is None:
        raise FeedNotFound(f"No feed with id {feed_id}")
    return (session.scalar(select(func.max(table.version)).where(table.feed_id == feed_id)) or 0) + 1


class FeedProfileRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_version(self, **fields: Any) -> FeedProfile:
        draft = FeedProfileDraft(**fields)
        row = Row(**draft.model_dump(mode="json"), version=next_version(self._session, Row, draft.feed_id))
        row.feed_id, row.fetch_id = draft.feed_id, draft.fetch_id  # UUIDs, not their JSON text
        self._session.add(row)
        self._session.flush()
        return FeedProfile.model_validate(row)

    def get(self, profile_id: UUID) -> FeedProfile | None:
        row = self._session.get(Row, profile_id)
        return None if row is None else FeedProfile.model_validate(row)

    def latest(self, feed_id: UUID) -> FeedProfile | None:
        row = self._session.scalars(select(Row).where(Row.feed_id == feed_id).order_by(Row.version.desc()).limit(1)).first()
        return None if row is None else FeedProfile.model_validate(row)

    def history(self, feed_id: UUID) -> list[FeedProfile]:
        """Oldest first."""
        return [FeedProfile.model_validate(r)
                for r in self._session.scalars(select(Row).where(Row.feed_id == feed_id).order_by(Row.version))]
