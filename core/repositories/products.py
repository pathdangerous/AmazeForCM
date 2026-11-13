"""ProductRepository: data products and the glossary elements each one holds.

A product's elements are written once, when the product is created; product_elements is append-only.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.product import Product, ProductDraft, ProductWithElements
from core.repositories.errors import (
    DuplicateProductCode,
    ProductNotFound,
    RequirementNotFound,
    UnknownGlossaryElement,
)
from core.repositories.tables import GlossaryElementRow, ProductElementRow, RequirementRow
from core.repositories.tables import ProductRow as Row


class ProductRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, **fields: Any) -> Product:
        draft = ProductDraft(**fields)
        if self._session.scalar(select(Row.id).where(Row.code == draft.code)) is not None:
            raise DuplicateProductCode(f"A product with code {draft.code!r} already exists")
        if draft.requirement_id is not None and self._session.get(RequirementRow, draft.requirement_id) is None:
            raise RequirementNotFound(f"No requirement with id {draft.requirement_id}")
        known = set(
            self._session.scalars(select(GlossaryElementRow.key).where(GlossaryElementRow.key.in_(draft.element_keys)))
        )
        unknown = [key for key in draft.element_keys if key not in known]
        if unknown:
            raise UnknownGlossaryElement(f"No glossary element with key {', '.join(map(repr, unknown))}")
        row = Row(**draft.model_dump(exclude={"element_keys"}))
        self._session.add(row)
        self._session.flush()
        self._session.add_all(ProductElementRow(product_id=row.id, element_key=key) for key in draft.element_keys)
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

    def elements_of(self, product_id: UUID) -> list[str]:
        """The keys of the product's elements, in alphabetical order."""
        self.get(product_id)  # raises ProductNotFound
        query = (
            select(ProductElementRow.element_key)
            .where(ProductElementRow.product_id == product_id)
            .order_by(ProductElementRow.element_key)
        )
        return list(self._session.scalars(query))

    def list_with_elements(self, domain: str | None = None) -> list[ProductWithElements]:
        """Every product (by name, then code) with its element keys, read in two queries."""
        keys: dict[UUID, list[str]] = defaultdict(list)
        rows = self._session.execute(
            select(ProductElementRow.product_id, ProductElementRow.element_key).order_by(ProductElementRow.element_key)
        )
        for product_id, key in rows:
            keys[product_id].append(key)
        return [ProductWithElements(**p.model_dump(), element_keys=keys[p.id]) for p in self.list(domain)]
