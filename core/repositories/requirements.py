"""RequirementRepository: requests for data that no product or known source has (step 01)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.requirement import Requirement, RequirementDraft, RequirementStatus
from core.repositories.errors import RequirementNotFound
from core.repositories.tables import RequirementRow as Row

_STATUS = TypeAdapter(RequirementStatus)


class RequirementRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, **fields: Any) -> Requirement:
        row = Row(**RequirementDraft(**fields).model_dump(), status="defined")
        self._session.add(row)
        self._session.flush()
        return Requirement.model_validate(row)

    def get(self, requirement_id: UUID) -> Requirement:
        return Requirement.model_validate(self._row(requirement_id))

    def list(self, requester_id: str | None = None) -> list[Requirement]:
        query = select(Row).order_by(Row.created_at.desc(), Row.id)
        if requester_id is not None:
            query = query.where(Row.requester_id == requester_id)
        return [Requirement.model_validate(r) for r in self._session.scalars(query)]

    def set_status(self, requirement_id: UUID, status: RequirementStatus) -> Requirement:
        status = _STATUS.validate_python(status)
        row = self._row(requirement_id)
        row.status = status
        self._session.flush()
        return Requirement.model_validate(row)

    def _row(self, requirement_id: UUID) -> Row:
        row = self._session.get(Row, requirement_id)
        if row is None:
            raise RequirementNotFound(f"No requirement with id {requirement_id}")
        return row
