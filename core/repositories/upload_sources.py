"""UploadSourceRepository: the sources uploaded files are (phase 7.8, task 2.4). Insert only (append-only table).

Flushes, never commits; the caller owns the transaction. The database checks that the feed reads the row's code
through the dropzone, so the feed is created first.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.upload_source import UploadSource, UploadSourceDraft
from core.repositories.errors import UploadSourceNotFound
from core.repositories.tables import UploadSourceRow as Row
from ports.connectors.catalogue import SourceEntry


class UploadSourceRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, **fields: Any) -> UploadSource:
        draft = UploadSourceDraft(**fields)
        row = Row(**draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return UploadSource.model_validate(row)

    def get_by_code(self, code: str) -> UploadSource:
        row = self._session.scalars(select(Row).where(Row.code == code)).one_or_none()
        if row is None:
            raise UploadSourceNotFound(f"No upload source with code {code!r}")
        return UploadSource.model_validate(row)

    def get_by_feed(self, feed_id: UUID) -> UploadSource | None:
        row = self._session.scalars(select(Row).where(Row.feed_id == feed_id)).one_or_none()
        return None if row is None else UploadSource.model_validate(row)

    def list(self) -> list[UploadSource]:
        """Oldest first."""
        return [UploadSource.model_validate(r) for r in self._session.scalars(select(Row).order_by(Row.created_at, Row.id))]

    def entries(self) -> list[SourceEntry]:
        """Every upload as a catalogue entry, for SourceCatalogue.with_uploads()."""
        return [source.as_entry() for source in self.list()]
