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
from core.repositories.errors import DuplicateProductCode, ProductNotFound, RequirementNotFound
from core.repositories.glossary import GlossaryRepository
from core.repositories.tables import ProductElementRow, RequirementRow
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
        GlossaryRepository(self._session).require_keys(draft.element_keys)  # raises UnknownGlossaryElement
        row = Row(**draft.model_dump(exclude={"element_keys"}))
        self._session.add(row)
        self._session.flush()
        self._session.add_all(ProductElementRow(product_id=row.id, element_key=key) for key in draft.element_keys)
        self._session.flush()
        return Product.model_validate(row)

    def add_elements(self, product_id: UUID, element_keys: list[str]) -> None:
        """Phase 7.19: elements the feed's mappings gained since the first release join the product. product_elements is
        append-only, so this only adds rows; a key the product already holds is skipped, and a key that is not a glossary
        element raises UnknownGlossaryElement."""
        held = set(self.elements_of(product_id))
        new = [k for k in dict.fromkeys(element_keys) if k not in held]
        GlossaryRepository(self._session).require_keys(new)
        self._session.add_all(ProductElementRow(product_id=product_id, element_key=key) for key in new)
        self._session.flush()

    def get(self, product_id: UUID) -> Product:
        row = self._session.get(Row, product_id)
        if row is None:
            raise ProductNotFound(f"No product with id {product_id}")
        return Product.model_validate(row)

    def for_feed(self, feed_id: UUID) -> Product | None:
        """The product created from this feed at its first release (migration 0019: at most one), if any."""
        row = self._session.scalars(select(Row).where(Row.feed_id == feed_id)).one_or_none()
        return None if row is None else Product.model_validate(row)

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
