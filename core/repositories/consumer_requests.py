"""ConsumerRequestRepository: the log of every "Request this data". Insert only; nothing is ever changed."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.consumer_request import ConsumerRequest, ConsumerRequestDraft
from core.repositories.errors import ConsumerRequestNotFound, RequirementNotFound
from core.repositories.glossary import GlossaryRepository
from core.repositories.tables import ConsumerRequestRow as Row
from core.repositories.tables import RequirementRow


class ConsumerRequestRepository:
    def __init__(self, session: Session):
        self._session = session

    def record(self, **fields: Any) -> ConsumerRequest:
        draft = ConsumerRequestDraft(**fields)
        # element_keys is an array, so the database's glossary-key trigger cannot guard it.
        GlossaryRepository(self._session).require_keys(draft.element_keys)  # raises UnknownGlossaryElement
        if draft.requirement_id is not None and self._session.get(RequirementRow, draft.requirement_id) is None:
            raise RequirementNotFound(f"No requirement with id {draft.requirement_id}")
        row = Row(**draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return ConsumerRequest.model_validate(row)

    def get_for(self, requester_id: str, request_id: UUID) -> ConsumerRequest:
        """One of the requester's own requests. Another requester's request is "not found" too, so the
        caller cannot learn that it exists."""
        row = self._session.get(Row, request_id)
        if row is None or row.requester_id != requester_id:
            raise ConsumerRequestNotFound(f"No request with id {request_id}")
        return ConsumerRequest.model_validate(row)

    def list_for(self, requester_id: str) -> list[ConsumerRequest]:
        """The requester's own requests, newest first."""
        query = select(Row).where(Row.requester_id == requester_id).order_by(Row.created_at.desc(), Row.id)
        return [ConsumerRequest.model_validate(r) for r in self._session.scalars(query)]
