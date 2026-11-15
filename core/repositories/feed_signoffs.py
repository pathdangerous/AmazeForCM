"""FeedSignoffRepository: step 03's sign-offs, versioned per feed; the gaps and reasons are kept.

Insert only (feed_signoffs is append-only). The database checks what a sign-off rests on: the feed's
latest profile, its decided summary and the person who decided it (0014; decision D1). Flushes only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.profiling import FeedSignoff, FeedSignoffDraft
from core.repositories.feed_profiles import next_version
from core.repositories.tables import FeedSignoffRow as Row


class FeedSignoffRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_version(self, **fields: Any) -> FeedSignoff:
        draft = FeedSignoffDraft(**fields)
        row = Row(**draft.model_dump(), version=next_version(self._session, Row, draft.feed_id))
        self._session.add(row)
        self._session.flush()
        return FeedSignoff.model_validate(row)

    def latest(self, feed_id: UUID) -> FeedSignoff | None:
        row = self._session.scalars(select(Row).where(Row.feed_id == feed_id).order_by(Row.version.desc()).limit(1)).first()
        return None if row is None else FeedSignoff.model_validate(row)

    def history(self, feed_id: UUID) -> list[FeedSignoff]:
        """Oldest first."""
        return [FeedSignoff.model_validate(r)
                for r in self._session.scalars(select(Row).where(Row.feed_id == feed_id).order_by(Row.version))]
