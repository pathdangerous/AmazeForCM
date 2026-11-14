"""SourceFetchRepository: every fetch of a feed's source, fetched or source_down.

Insert only (source_fetches is append-only). The caller may choose the id, so the blob key
raw/<feed>/<date>/<fetch id>/<file> can be built before the row is written.
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.sourcing import SourceFetch, SourceFetchDraft
from core.repositories.errors import FeedNotFound
from core.repositories.tables import FeedRow
from core.repositories.tables import SourceFetchRow as Row


class SourceFetchRepository:
    def __init__(self, session: Session):
        self._session = session

    def record(self, *, feed_id: UUID, fetch_id: UUID | None = None, **fields: Any) -> SourceFetch:
        draft = SourceFetchDraft(**fields)
        if self._session.get(FeedRow, feed_id) is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        row = Row(id=fetch_id or uuid.uuid4(), feed_id=feed_id, **draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return SourceFetch.model_validate(row)

    def list_for_feed(self, feed_id: UUID) -> list[SourceFetch]:
        """Newest first."""
        query = select(Row).where(Row.feed_id == feed_id).order_by(Row.created_at.desc(), Row.id)
        return [SourceFetch.model_validate(r) for r in self._session.scalars(query)]

    def latest(self, feed_id: UUID) -> SourceFetch | None:
        return self._first(select(Row).where(Row.feed_id == feed_id))

    def latest_fetched(self, feed_id: UUID) -> SourceFetch | None:
        return self._first(select(Row).where(Row.feed_id == feed_id, Row.outcome == "fetched"))

    def _first(self, query) -> SourceFetch | None:
        row = self._session.scalars(query.order_by(Row.created_at.desc(), Row.id).limit(1)).first()
        return None if row is None else SourceFetch.model_validate(row)
