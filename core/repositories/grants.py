"""ProductGrantRepository: grant and revoke events on a product. Insert only; nothing is ever changed."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.entitlement import GrantDraft, GrantEvent
from core.repositories.errors import ProductNotFound
from core.repositories.tables import ProductGrantRow as Row
from core.repositories.tables import ProductRow


class ProductGrantRepository:
    def __init__(self, session: Session):
        self._session = session

    def record(self, *, product_id: UUID, **fields: Any) -> GrantEvent:
        draft = GrantDraft(**fields)
        self._require_product(product_id)
        row = Row(product_id=product_id, **draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return GrantEvent.model_validate(row)

    def events_for(self, product_id: UUID) -> list[GrantEvent]:
        """Every event of the product, oldest first (by seq)."""
        self._require_product(product_id)
        query = select(Row).where(Row.product_id == product_id).order_by(Row.seq)
        return [GrantEvent.model_validate(r) for r in self._session.scalars(query)]

    def events_by_product(self, product_ids: Iterable[UUID]) -> dict[UUID, list[GrantEvent]]:
        """The events of many products in one query, each list oldest first. A product without events
        has no entry."""
        ids = list(product_ids)
        events: dict[UUID, list[GrantEvent]] = defaultdict(list)
        if ids:
            for row in self._session.scalars(select(Row).where(Row.product_id.in_(ids)).order_by(Row.seq)):
                events[row.product_id].append(GrantEvent.model_validate(row))
        return dict(events)

    def _require_product(self, product_id: UUID) -> None:
        if self._session.get(ProductRow, product_id) is None:
            raise ProductNotFound(f"No product with id {product_id}")
