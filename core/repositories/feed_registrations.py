"""FeedRegistrationRepository: step 04's registrations, versioned per feed (phase 4 writes version 1).

Insert only (feed_registrations is append-only). The database needs the feed's latest sign-off,
accepting, on its latest profile (0014). Flushes only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.feed import FeedRegistration, FeedRegistrationDraft
from core.repositories.feed_profiles import next_version
from core.repositories.tables import FeedRegistrationRow as Row


class FeedRegistrationRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_version(self, **fields: Any) -> FeedRegistration:
        draft = FeedRegistrationDraft(**fields)
        row = Row(**draft.model_dump(), version=next_version(self._session, Row, draft.feed_id))
        self._session.add(row)
        self._session.flush()
        return FeedRegistration.model_validate(row)

    def current(self, feed_id: UUID) -> FeedRegistration | None:
        row = self._session.scalars(select(Row).where(Row.feed_id == feed_id).order_by(Row.version.desc()).limit(1)).first()
        return None if row is None else FeedRegistration.model_validate(row)
