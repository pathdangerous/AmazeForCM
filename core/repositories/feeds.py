"""FeedRepository: the feed registry. Candidates are created; registration (step 04) needs an owner."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from core.domain.feed import Feed, FeedDraft, FeedStage
from core.repositories.errors import (
    DuplicateFeedCode,
    DuplicateFeedSource,
    FeedHasNoOwner,
    FeedNotACandidate,
    FeedNotFound,
    RequirementNotFound,
)
from core.repositories.tables import FeedRow as Row
from core.repositories.tables import RequirementRow, SourceChoiceRow


class FeedRepository:
    def __init__(self, session: Session):
        self._session = session

    def create_candidate(self, **fields: Any) -> Feed:
        draft = FeedDraft(**fields)
        if self._session.scalar(select(Row.id).where(Row.code == draft.code)) is not None:
            raise DuplicateFeedCode(f"A feed with code {draft.code!r} already exists")
        if draft.source_code is not None and self.get_by_source(draft.source_code) is not None:
            raise DuplicateFeedSource(f"A feed already reads source {draft.source_code!r}")
        if draft.requirement_id is not None and self._session.get(RequirementRow, draft.requirement_id) is None:
            raise RequirementNotFound(f"No requirement with id {draft.requirement_id}")
        row = Row(**draft.model_dump(), stage="candidate")
        self._session.add(row)
        self._session.flush()
        return Feed.model_validate(row)

    def get(self, feed_id: UUID) -> Feed:
        return Feed.model_validate(self._row(feed_id))

    def get_by_source(self, source_code: str) -> Feed | None:
        """The feed that reads this catalogue source, if one exists (one feed per source)."""
        row = self._session.scalars(select(Row).where(Row.source_code == source_code)).one_or_none()
        return None if row is None else Feed.model_validate(row)

    def list_to_fetch(self) -> list[Feed]:
        """Feeds the daily job fetches (phase 3 plan, assumption 54, as changed by Q4): a connector, not
        rejected, and either registered or currently selected by some requirement (its highest
        source_choices version is a selection of the feed). A candidate feed every requirement released
        is idle. Oldest first."""
        latest = (select(SourceChoiceRow.requirement_id, func.max(SourceChoiceRow.version).label("version"))
                  .group_by(SourceChoiceRow.requirement_id).subquery())
        selected = (
            select(SourceChoiceRow.id)
            .join(latest, (latest.c.requirement_id == SourceChoiceRow.requirement_id)
                  & (latest.c.version == SourceChoiceRow.version))
            .where(SourceChoiceRow.feed_id == Row.id, SourceChoiceRow.action == "selected")
            .exists()
        )
        query = (
            select(Row)
            .where(Row.connector.is_not(None), Row.stage != "rejected", or_(Row.stage == "registered", selected))
            .order_by(Row.created_at, Row.id)
        )
        return [Feed.model_validate(r) for r in self._session.scalars(query)]

    def get_by_code(self, code: str) -> Feed:
        row = self._session.scalars(select(Row).where(Row.code == code)).one_or_none()
        if row is None:
            raise FeedNotFound(f"No feed with code {code!r}")
        return Feed.model_validate(row)

    def list(self, stage: FeedStage | None = None) -> list[Feed]:
        query = select(Row).order_by(Row.created_at.desc(), Row.id)
        if stage is not None:
            query = query.where(Row.stage == stage)
        return [Feed.model_validate(r) for r in self._session.scalars(query)]

    def register(self, feed_id: UUID, owner_id: str | None = None) -> Feed:
        row = self._row(feed_id, lock=True)
        if row.stage != "candidate":
            raise FeedNotACandidate(f"Feed {row.code!r} is {row.stage}, not a candidate")
        owner = (owner_id or "").strip() or row.owner_id
        if not owner:
            raise FeedHasNoOwner(f"Feed {row.code!r} has no owner; name one to register it")
        row.owner_id, row.stage, row.registered_at = owner, "registered", datetime.now(UTC)
        self._session.flush()
        return Feed.model_validate(row)

    def _row(self, feed_id: UUID, *, lock: bool = False) -> Row:
        query = select(Row).where(Row.id == feed_id)
        if lock:
            query = query.with_for_update()
        row = self._session.scalars(query).one_or_none()
        if row is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        return row
