"""StandardRecordRepository: the rows of a loaded file in the standard model (step 13, phase 6). Append-only;
trg_standard_records_b_load (migration 0018, G11) lets a record in only for a received load of its feed."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.domain.load import StandardRecord, StandardRecordDraft
from core.repositories.tables import StandardRecordRow as Row


class StandardRecordRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_many(self, *, load_id: UUID, feed_id: UUID, records: Sequence[StandardRecordDraft]) -> int:
        """Insert the records of one load; returns how many."""
        self._session.add_all(Row(load_id=load_id, feed_id=feed_id, **record.model_dump()) for record in records)
        self._session.flush()
        return len(records)

    def count(self, load_id: UUID) -> int:
        return self._session.scalar(select(func.count()).select_from(Row).where(Row.load_id == load_id)) or 0

    def count_flagged(self, load_id: UUID) -> int:
        """The records of the load that at least one rule flagged."""
        return self._session.scalar(select(func.count()).select_from(Row).where(
            Row.load_id == load_id, func.cardinality(Row.flags) > 0)) or 0

    def iter_for_load(self, load_id: UUID, limit: int | None = None, batch: int = 1000) -> Iterator[StandardRecord]:
        """Every record of the load (or the first `limit`), by row number, read in batches."""
        query = select(Row).where(Row.load_id == load_id).order_by(Row.row_number)
        if limit is not None:
            query = query.limit(limit)
        for row in self._session.scalars(query.execution_options(yield_per=batch)):
            yield StandardRecord.model_validate(row)

    def list_for_load(self, load_id: UUID, limit: int = 50) -> list[StandardRecord]:
        """By row number."""
        query = select(Row).where(Row.load_id == load_id).order_by(Row.row_number).limit(limit)
        return [StandardRecord.model_validate(r) for r in self._session.scalars(query)]
