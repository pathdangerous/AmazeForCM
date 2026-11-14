"""RequirementScopeRepository: the scope a requester confirmed (S1), versioned per requirement.

Insert only (requirement_scopes is append-only). The database accepts a row only for an approved scope
proposal of the same requirement that holds exactly this scope, confirmed by the requirement's own
requester (migration 0009). The repository flushes and never commits.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.domain.sourcing import RequirementScope, RequirementScopeDraft
from core.repositories.errors import RequirementNotFound
from core.repositories.tables import RequirementRow
from core.repositories.tables import RequirementScopeRow as Row


class RequirementScopeRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_version(self, *, requirement_id: UUID, proposal_id: UUID, confirmed_by: str,
                    **fields: Any) -> RequirementScope:
        draft = RequirementScopeDraft(**fields)
        if self._session.scalars(
            select(RequirementRow.id).where(RequirementRow.id == requirement_id).with_for_update()
        ).one_or_none() is None:
            raise RequirementNotFound(f"No requirement with id {requirement_id}")
        last = self._session.scalar(select(func.max(Row.version)).where(Row.requirement_id == requirement_id))
        row = Row(requirement_id=requirement_id, version=(last or 0) + 1, proposal_id=proposal_id,
                  confirmed_by=confirmed_by, **draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return RequirementScope.model_validate(row)

    def current(self, requirement_id: UUID) -> RequirementScope | None:
        row = self._session.scalars(
            select(Row).where(Row.requirement_id == requirement_id).order_by(Row.version.desc()).limit(1)
        ).first()
        return None if row is None else RequirementScope.model_validate(row)
