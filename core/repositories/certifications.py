"""LoadCertificationRepository: step 15's trust score of one loaded scheduled load (phase 7, migration 0019).

Append-only. The database recounts the load's records and flags and checks the score and the outcome (G14);
this repository writes what step 15 worked out and reads it back.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.load import LoadCertification, LoadCertificationDraft
from core.repositories.tables import LoadCertificationRow as Row


class LoadCertificationRepository:
    def __init__(self, session: Session):
        self._session = session

    def add(self, **fields: Any) -> LoadCertification:
        draft = LoadCertificationDraft(**fields)
        row = Row(**draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return LoadCertification.model_validate(row)

    def for_load(self, load_id: UUID) -> LoadCertification | None:
        row = self._session.scalars(select(Row).where(Row.load_id == load_id)).one_or_none()
        return None if row is None else LoadCertification.model_validate(row)
