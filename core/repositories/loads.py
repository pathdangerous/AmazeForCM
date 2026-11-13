"""LoadRepository: deliveries of registered feeds."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.load import Load, LoadDraft, LoadStatus
from core.repositories.errors import FeedNotFound, FeedNotRegistered, LoadNotFound
from core.repositories.tables import FeedRow
from core.repositories.tables import LoadRow as Row

_STATUS = TypeAdapter(LoadStatus)


class LoadRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, *, feed_id: UUID, **fields: Any) -> Load:
        draft = LoadDraft(**fields)
        feed = self._session.get(FeedRow, feed_id)
        if feed is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        if feed.stage != "registered":
            raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}; loads arrive only for registered feeds")
        values = draft.model_dump(exclude_none=True)  # received_at defaults to the moment of insert
        row = Row(feed_id=feed_id, status="received", **values)
        self._session.add(row)
        self._session.flush()
        return Load.model_validate(row)

    def get(self, load_id: UUID) -> Load:
        return Load.model_validate(self._row(load_id))

    def list_for_feed(self, feed_id: UUID) -> list[Load]:
        query = select(Row).where(Row.feed_id == feed_id).order_by(Row.received_at.desc(), Row.created_at.desc())
        return [Load.model_validate(r) for r in self._session.scalars(query)]

    def set_status(self, load_id: UUID, status: LoadStatus) -> Load:
        status = _STATUS.validate_python(status)
        row = self._row(load_id)
        row.status = status
        self._session.flush()
        return Load.model_validate(row)

    def _row(self, load_id: UUID) -> Row:
        row = self._session.get(Row, load_id)
        if row is None:
            raise LoadNotFound(f"No load with id {load_id}")
        return row
