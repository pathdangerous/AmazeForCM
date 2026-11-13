"""ProductRepository: data products."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.product import Product, ProductDraft
from core.repositories.errors import DuplicateProductCode, ProductNotFound, RequirementNotFound
from core.repositories.tables import ProductRow as Row
from core.repositories.tables import RequirementRow


class ProductRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, **fields: Any) -> Product:
        draft = ProductDraft(**fields)
        if self._session.scalar(select(Row.id).where(Row.code == draft.code)) is not None:
            raise DuplicateProductCode(f"A product with code {draft.code!r} already exists")
        if draft.requirement_id is not None and self._session.get(RequirementRow, draft.requirement_id) is None:
            raise RequirementNotFound(f"No requirement with id {draft.requirement_id}")
        row = Row(**draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return Product.model_validate(row)

    def get(self, product_id: UUID) -> Product:
        row = self._session.get(Row, product_id)
        if row is None:
            raise ProductNotFound(f"No product with id {product_id}")
        return Product.model_validate(row)

    def get_by_code(self, code: str) -> Product:
        row = self._session.scalars(select(Row).where(Row.code == code)).one_or_none()
        if row is None:
            raise ProductNotFound(f"No product with code {code!r}")
        return Product.model_validate(row)

    def list(self, domain: str | None = None) -> list[Product]:
        query = select(Row).order_by(Row.name, Row.code)
        if domain is not None:
            query = query.where(Row.domain == domain)
        return [Product.model_validate(r) for r in self._session.scalars(query)]
