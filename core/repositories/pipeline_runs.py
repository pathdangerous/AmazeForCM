"""PipelineRunRepository: the runs of the code pipeline (phase 7.17, migration 0037). Append-only; the database checks that
the outcome is the stages'. Repositories flush but never commit; the caller owns the transaction."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.pipeline_run import PipelineRun, PipelineStage
from core.repositories.errors import FeedNotFound
from core.repositories.tables import FeedRow
from core.repositories.tables import PipelineRunRow as Row


class PipelineRunRepository:
    def __init__(self, session: Session):
        self._session = session

    def add(self, *, feed_id: UUID, code_sha256: str, commit_hash: str | None, commit_created: bool,
            stages: Sequence[PipelineStage], outcome: str, run_by: str) -> PipelineRun:
        if self._session.get(FeedRow, feed_id) is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        row = Row(feed_id=feed_id, code_sha256=code_sha256, commit_hash=commit_hash, commit_created=commit_created,
                  stages=[s.model_dump(mode="json") for s in stages], outcome=outcome, run_by=run_by)
        self._session.add(row)
        self._session.flush()
        self._session.refresh(row)  # created_at is the database's clock
        return PipelineRun.model_validate(row)

    def list_for_feed(self, feed_id: UUID) -> list[PipelineRun]:
        """Every run of the feed, oldest first."""
        rows = self._session.scalars(select(Row).where(Row.feed_id == feed_id).order_by(Row.created_at, Row.id))
        return [PipelineRun.model_validate(r) for r in rows]

    def has_passed(self, feed_id: UUID, code_sha256: str) -> bool:
        return self._session.scalar(select(Row.id).where(
            Row.feed_id == feed_id, Row.code_sha256 == code_sha256, Row.outcome == "passed").limit(1)) is not None
