"""RequirementRepository: requests for data that no product or known source has (step 01).

A requirement's elements and terms are written once, with the requirement; both tables are append-only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.requirement import REQUIREMENT_TRANSITIONS, Requirement, RequirementDraft, RequirementStatus
from core.repositories.errors import RequirementNotFound, RequirementNotInStatus
from core.repositories.glossary import GlossaryRepository
from core.repositories.tables import RequirementElementRow, RequirementTermRow
from core.repositories.tables import RequirementRow as Row

_STATUS = TypeAdapter(RequirementStatus)


class RequirementRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, **fields: Any) -> Requirement:
        draft = RequirementDraft(**fields)
        GlossaryRepository(self._session).require_keys(draft.element_keys)  # raises UnknownGlossaryElement
        row = Row(**draft.model_dump(exclude={"element_keys", "terms"}), status="defined")
        self._session.add(row)
        self._session.flush()
        self._session.add_all(RequirementElementRow(requirement_id=row.id, element_key=k) for k in draft.element_keys)
        self._session.add_all(RequirementTermRow(requirement_id=row.id, term=t) for t in draft.terms)
        self._session.flush()
        return self._models([row])[0]

    def get(self, requirement_id: UUID) -> Requirement:
        return self._models([self._row(requirement_id)])[0]

    def lock(self, requirement_id: UUID) -> Requirement:
        """The requirement, with its row locked until the transaction ends. Every step that changes a
        requirement calls this first, so two requests on one requirement run one after the other and the
        second sees what the first committed (review fix 1)."""
        return self._models([self._row(requirement_id, lock=True)])[0]

    def list(self, requester_id: str | None = None) -> list[Requirement]:
        query = select(Row).order_by(Row.created_at.desc(), Row.id)
        if requester_id is not None:
            query = query.where(Row.requester_id == requester_id)
        return self._models(self._session.scalars(query).all())

    def ids_in_status(self, status: RequirementStatus) -> list[UUID]:
        """The ids of the requirements with this status, oldest first."""
        status = _STATUS.validate_python(status)
        query = select(Row.id).where(Row.status == status).order_by(Row.created_at, Row.id)
        return list(self._session.scalars(query))

    def advance(self, requirement_id: UUID, to: RequirementStatus) -> Requirement:
        """Move the requirement to `to`, only along REQUIREMENT_TRANSITIONS. The row is locked, so two
        requests cannot both make the same move."""
        to = _STATUS.validate_python(to)
        row = self._row(requirement_id, lock=True)
        if (row.status, to) not in REQUIREMENT_TRANSITIONS:
            raise RequirementNotInStatus(
                f"Requirement {requirement_id} cannot move from {row.status} to {to}"
            )
        row.status = to
        self._session.flush()
        return self._models([row])[0]

    def _row(self, requirement_id: UUID, *, lock: bool = False) -> Row:
        query = select(Row).where(Row.id == requirement_id)
        if lock:
            query = query.with_for_update()
        row = self._session.scalars(query).one_or_none()
        if row is None:
            raise RequirementNotFound(f"No requirement with id {requirement_id}")
        return row

    def _models(self, rows: Sequence[Row]) -> list[Requirement]:
        """The requirements with their elements and terms, read in two queries for the whole list."""
        ids = [row.id for row in rows]
        elements: dict[UUID, list[str]] = defaultdict(list)
        terms: dict[UUID, list[str]] = defaultdict(list)
        if ids:
            element_rows = self._session.execute(
                select(RequirementElementRow.requirement_id, RequirementElementRow.element_key)
                .where(RequirementElementRow.requirement_id.in_(ids))
                .order_by(RequirementElementRow.element_key)
            )
            for requirement_id, key in element_rows:
                elements[requirement_id].append(key)
            term_rows = self._session.execute(
                select(RequirementTermRow.requirement_id, RequirementTermRow.term)
                .where(RequirementTermRow.requirement_id.in_(ids))
                .order_by(RequirementTermRow.term)
            )
            for requirement_id, term in term_rows:
                terms[requirement_id].append(term)
        return [
            Requirement(
                id=row.id, created_at=row.created_at, requester_id=row.requester_id, title=row.title,
                description=row.description, status=row.status, elements=elements[row.id], terms=terms[row.id],
            )
            for row in rows
        ]
