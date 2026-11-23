"""CodeVersionRepository: the generated code of a feed, one version per go-live approval (phase 7.12, T3).

Append-only; trg_code_versions_guard (migration 0030) checks the version is the next one and that `sha256` is the hash
of `files`. Repositories flush but never commit; the caller owns the transaction and holds the feed row lock.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.domain.code_version import CodeVersion
from core.repositories.errors import FeedNotFound, RepositoryError
from core.repositories.tables import CodeVersionRow as Row
from core.repositories.tables import FeedRow


class CodeVersionNotFound(RepositoryError):
    """No such code version for this feed. The API answers 404."""


class CodeVersionRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_version(self, *, feed_id: UUID, files: dict[str, str], sha256: str, mapping_ids: Sequence[UUID],
                    rule_ids: Sequence[UUID], transformation_ids: Sequence[UUID], test_load_id: UUID | None,
                    test_load_result: dict[str, Any], approved_by: str) -> CodeVersion:
        if self._session.get(FeedRow, feed_id) is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        latest = self._session.scalar(select(func.max(Row.version)).where(Row.feed_id == feed_id))
        row = Row(feed_id=feed_id, version=(latest or 0) + 1, sha256=sha256, files=files, mapping_ids=list(mapping_ids),
                  rule_ids=list(rule_ids), transformation_ids=list(transformation_ids), test_load_id=test_load_id,
                  test_load_result=test_load_result, approved_by=approved_by)
        self._session.add(row)
        self._session.flush()
        return CodeVersion.model_validate(row)

    def list_for_feed(self, feed_id: UUID) -> list[CodeVersion]:
        rows = self._session.scalars(select(Row).where(Row.feed_id == feed_id).order_by(Row.version))
        return [CodeVersion.model_validate(r) for r in rows]

    def latest(self, feed_id: UUID) -> CodeVersion | None:
        found = self.list_for_feed(feed_id)
        return found[-1] if found else None

    def get(self, feed_id: UUID, version: int) -> CodeVersion:
        row = self._session.scalars(select(Row).where(Row.feed_id == feed_id, Row.version == version)).one_or_none()
        if row is None:
            raise CodeVersionNotFound(f"Feed {feed_id} has no code version {version}")
        return CodeVersion.model_validate(row)
