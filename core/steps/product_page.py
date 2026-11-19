"""The consumer's product pages (phase 7.8, task 7.1; plan assumption 43): the catalogue by domain, one product's page
with its data dictionary, a preview of the first 100 rows, and the rows for the CSV and Excel downloads.

Read only, no model call, no write. Who may see the rows is decided by core/entitlement (rules only), exactly as
step 17 decides it: every product is listed to everyone, the rows go to the entitled. The rows are those of the
product's feed's latest released load. A column's label is the `rename` label of that load's standard stage, or the
element's name.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.domain.glossary import GlossaryElement
from core.domain.product import ProductWithElements
from core.entitlement.decide import decide, route_to_access
from core.repositories.configuration import MappingRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.grants import ProductGrantRepository
from core.repositories.loads import LoadRepository
from core.repositories.products import ProductRepository
from core.repositories.standard_records import StandardRecordRepository
from core.steps.result import StepResult

PREVIEW_ROWS = 100


class UserLike(Protocol):
    id: str
    roles: Sequence[str]


class IdentityLike(Protocol):
    def get_user(self, user_id: str) -> Any: ...


class Owner(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str


class Freshness(BaseModel):
    model_config = ConfigDict(frozen=True)

    released_at: datetime
    rows: int


class CatalogueProduct(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    description: str
    owner_id: str
    owner_name: str
    visibility: str
    freshness: Freshness | None
    access: Literal["entitled", "not_entitled_visible"]
    route: str | None  # "Ask <owner> for access to <product>" when not entitled


class DomainGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    domain: str
    label: str
    products: list[CatalogueProduct]


class DictionaryRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    element_key: str
    name: str
    definition: str
    data_type: str
    unit: str | None
    label: str  # the column header: an approved rename's label, else the element's name


class ProductPage(StepResult):
    outcome: Literal["product_page"] = "product_page"
    code: str
    name: str
    description: str
    domain: str
    domain_label: str
    owner: Owner
    visibility: str
    access: Literal["entitled", "not_entitled_visible"]
    route: str | None
    freshness: Freshness | None
    columns: list[str]  # element keys, in the feed's mapping order
    dictionary: list[DictionaryRow]


class ProductNotFound(StepResult):
    outcome: Literal["product_not_found"] = "product_not_found"
    code: str


class NotEntitled(StepResult):
    outcome: Literal["not_entitled"] = "not_entitled"
    route: str


class NoData(StepResult):
    outcome: Literal["no_data"] = "no_data"


class Preview(StepResult):
    outcome: Literal["preview"] = "preview"
    columns: list[str]  # labels
    rows: list[list[Any]]


@dataclass
class Export:
    """What a download needs: the header (labels), each column's data type, and every row as a list of values."""

    columns: list[str]
    data_types: list[str]
    rows: Iterator[list[Any]]


def domain_label(domain: str) -> str:
    return domain.replace("_", " ").capitalize()


def _owner_name(identity: IdentityLike, owner_id: str) -> str:
    user = identity.get_user(owner_id)
    return user.display_name if user else owner_id


def _access(session: Session, products: Sequence[ProductWithElements], user: UserLike, identity: IdentityLike):
    """{product id: (entitled, route)}: decide() on each product's grant events."""
    events = ProductGrantRepository(session).events_by_product(p.id for p in products)
    out = {}
    for p in products:
        access = decide(visibility=p.visibility, owner_id=p.owner_id, user_id=user.id, roles=list(user.roles),
                        events=events.get(p.id, []))
        out[p.id] = (access.entitled, None if access.entitled else route_to_access(_owner_name(identity, p.owner_id), p.name))
    return out


def _freshness(session: Session, product: ProductWithElements) -> tuple[Freshness | None, Any]:
    """(freshness, the latest released load) of the product's feed; (None, None) without one."""
    if product.feed_id is None:
        return None, None
    load = LoadRepository(session).latest_released(product.feed_id)
    if load is None:
        return None, None
    rows = StandardRecordRepository(session).count(load.id)
    return Freshness(released_at=load.finished_at or load.received_at, rows=rows), load


def catalogue(session: Session, *, user: UserLike, identity: IdentityLike) -> list[DomainGroup]:
    products = ProductRepository(session).list_with_elements()
    access = _access(session, products, user, identity)
    groups: dict[str, list[CatalogueProduct]] = {}
    for p in products:
        entitled, route = access[p.id]
        groups.setdefault(p.domain, []).append(CatalogueProduct(
            code=p.code, name=p.name, description=p.description, owner_id=p.owner_id,
            owner_name=_owner_name(identity, p.owner_id), visibility=p.visibility,
            freshness=_freshness(session, p)[0], access="entitled" if entitled else "not_entitled_visible",
            route=route))
    return [DomainGroup(domain=d, label=domain_label(d), products=groups[d]) for d in sorted(groups)]


def _by_code(session: Session, code: str) -> ProductWithElements | None:
    return next((p for p in ProductRepository(session).list_with_elements() if p.code == code), None)


def _columns(session: Session, product: ProductWithElements) -> list[str]:
    """The product's element keys in the feed's current mapping order (by source field), then any not mapped."""
    held = set(product.element_keys)
    ordered: list[str] = []
    if product.feed_id is not None:
        for mapping in MappingRepository(session).current_for_feed(product.feed_id):
            if mapping.element_key in held and mapping.element_key not in ordered:
                ordered.append(mapping.element_key)
    return ordered + [k for k in product.element_keys if k not in ordered]


def _labels(session: Session, load_id) -> dict[str, str]:
    for stage in LoadRepository(session).stages(load_id):
        if stage.stage == "standard":
            return dict(stage.detail.get("labels") or {})
    return {}


def product_page(session: Session, *, code: str, user: UserLike, identity: IdentityLike) -> ProductPage | ProductNotFound:
    product = _by_code(session, code)
    if product is None:
        return ProductNotFound(code=code)
    entitled, route = _access(session, [product], user, identity)[product.id]
    freshness, load = _freshness(session, product)
    labels = _labels(session, load.id) if load is not None else {}
    keys = _columns(session, product)
    elements = {e.key: e for e in GlossaryRepository(session).list_current() if e.key in set(keys)}
    return ProductPage(
        code=product.code, name=product.name, description=product.description, domain=product.domain,
        domain_label=domain_label(product.domain), owner=Owner(id=product.owner_id,
                                                              name=_owner_name(identity, product.owner_id)),
        visibility=product.visibility, access="entitled" if entitled else "not_entitled_visible", route=route,
        freshness=freshness, columns=keys,
        dictionary=[_dictionary_row(elements[k], labels.get(k)) for k in keys])


def _dictionary_row(element: GlossaryElement, label: str | None) -> DictionaryRow:
    return DictionaryRow(element_key=element.key, name=element.name, definition=element.definition,
                         data_type=element.data_type, unit=getattr(element, "unit", None) or None,
                         label=label or element.name)


def _rows_of(session: Session, code: str, user: UserLike, limit: int | None, identity: IdentityLike | None):
    """(Export | NotEntitled | NoData | ProductNotFound) for the product's latest released load."""
    product = _by_code(session, code)
    if product is None:
        return ProductNotFound(code=code)
    entitled, route = _access(session, [product], user, identity or _IdsOnly())[product.id]
    if not entitled:
        return NotEntitled(route=route or "")
    _, load = _freshness(session, product)
    if load is None:
        return NoData()
    keys = _columns(session, product)
    elements = {e.key: e for e in GlossaryRepository(session).list_current() if e.key in set(keys)}
    labels = _labels(session, load.id)
    records = StandardRecordRepository(session).iter_for_load(load.id, limit)
    return Export(columns=[labels.get(k) or elements[k].name for k in keys],
                  data_types=[elements[k].data_type for k in keys],
                  rows=([r.values.get(k) for k in keys] for r in records))


class _IdsOnly:
    """No identity port given: the owner is named by id in the route to access."""

    def get_user(self, user_id: str):
        return None


def preview(session: Session, *, code: str, user: UserLike, identity: IdentityLike | None = None,
            limit: int = PREVIEW_ROWS) -> Preview | NotEntitled | NoData | ProductNotFound:
    found = _rows_of(session, code, user, limit, identity)
    if not isinstance(found, Export):
        return found
    return Preview(columns=found.columns, rows=list(found.rows))


def export_rows(session: Session, *, code: str, user: UserLike,
                identity: IdentityLike | None = None) -> Export | NotEntitled | NoData | ProductNotFound:
    """Every record of the latest released load, for a download."""
    return _rows_of(session, code, user, None, identity)
