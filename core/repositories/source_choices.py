"""SourceChoiceRepository: which source, and so which candidate feed, a requirement was handed to.

One row per requirement; insert only (source_choices is append-only). The repository flushes and
never commits.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.sourcing import SourceChoice
from core.repositories.errors import FeedNotFound, RequirementAlreadyHasSource, RequirementNotFound
from core.repositories.tables import FeedRow, RequirementRow
from core.repositories.tables import SourceChoiceRow as Row


class SourceChoiceRepository:
    def __init__(self, session: Session):
        self._session = session

    def record_known(self, *, requirement_id: UUID, feed_id: UUID, source_code: str) -> SourceChoice:
        """The requirement is handed to a known source (Known source? -> known_source)."""
        if self._session.get(RequirementRow, requirement_id) is None:
            raise RequirementNotFound(f"No requirement with id {requirement_id}")
        feed = self._session.get(FeedRow, feed_id)
        if feed is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        if feed.source_code != source_code:
            raise ValueError(f"Feed {feed.code!r} reads {feed.source_code!r}, not {source_code!r}")
        if self.for_requirement(requirement_id) is not None:
            raise RequirementAlreadyHasSource(f"Requirement {requirement_id} is already handed to a source")
        row = Row(requirement_id=requirement_id, feed_id=feed_id, source_code=source_code, basis="known_source")
        self._session.add(row)
        self._session.flush()
        return SourceChoice.model_validate(row)

    def for_requirement(self, requirement_id: UUID) -> SourceChoice | None:
        row = self._session.scalars(select(Row).where(Row.requirement_id == requirement_id)).one_or_none()
        return None if row is None else SourceChoice.model_validate(row)

    def requirements_for_feed(self, feed_id: UUID) -> list[UUID]:
        """Every requirement handed to this feed, oldest choice first."""
        query = select(Row.requirement_id).where(Row.feed_id == feed_id).order_by(Row.created_at, Row.id)
        return list(self._session.scalars(query))
