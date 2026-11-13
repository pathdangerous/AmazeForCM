"""Routing checks, in order: reuse, compose (stub), create_product, raise_requirement.

Decisions 3 and 5; phase 2 plan, assumptions 22 to 28. Deterministic code, read only.

E is the ticked element keys and T the ticked terms. C is the best cover of E (at most 3 products,
chosen by COVER_RULE) and R the elements of E that C does not hold.

1. T is not empty: raise_requirement for R and T (assumption 24).
2. R is empty and C is one product: reuse.
3. R is empty and C is two or three products: compose. A stub: nothing is built.
4. Every element of R is in the domains: create_product. Nothing is built either (step 20 is phase 7).
5. Otherwise raise_requirement for R, every uncovered element; the result names C and marks which
   elements of R are already in the domains.

Entitlement never changes the outcome. A restricted product still counts; entitlement only breaks a
tie between covers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from itertools import combinations
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.entitlement.decide import decide, route_to_access
from core.repositories.coverage import DomainCoverage
from core.repositories.grants import ProductGrantRepository
from core.repositories.products import ProductRepository
from core.steps.errors import NothingToRequest
from core.steps.result import StepResult

MAX_PRODUCTS = 3

COVER_RULE = (
    "the most elements of the request covered",
    "the fewest products",
    "the most products the requester is entitled to",
    "the fewest elements held beyond the request",
    "product codes in alphabetical order",
)


class CatalogueProduct(BaseModel):
    """A product as routing sees it: its elements and the requester's access."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    element_keys: list[str]
    entitled: bool
    route: str | None  # set when not entitled


class ProductAccess(BaseModel):
    """A product named in a routing result: what it covers of the request, and the requester's access."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    covers: list[str]
    entitled: bool
    route: str | None


class Reuse(StepResult):
    outcome: Literal["reuse"] = "reuse"
    product: ProductAccess


class Compose(StepResult):
    outcome: Literal["compose"] = "compose"
    products: list[ProductAccess]  # two or three, by code


class CreateProduct(StepResult):
    outcome: Literal["create_product"] = "create_product"
    elements: list[str]  # R: to be built from the domains
    covered_by: list[ProductAccess]


class RaiseRequirement(StepResult):
    outcome: Literal["raise_requirement"] = "raise_requirement"
    elements: list[str]  # R: every uncovered element
    terms: list[str]  # T
    in_domains: list[str]  # the part of R already in the domains
    covered_by: list[ProductAccess]


RoutingResult = Annotated[Reuse | Compose | CreateProduct | RaiseRequirement, Field(discriminator="outcome")]


def _best_cover(asked: set[str], catalogue: Sequence[CatalogueProduct]) -> tuple[CatalogueProduct, ...]:
    """Brute force over every combination of up to MAX_PRODUCTS products holding part of the request."""
    useful = [p for p in catalogue if asked & set(p.element_keys)]

    def rank(combo: tuple[CatalogueProduct, ...]):
        held = set().union(*(p.element_keys for p in combo))
        return (-len(asked & held), len(combo), -sum(p.entitled for p in combo), len(held - asked),
                sorted(p.code for p in combo))

    combos = [c for n in range(1, MAX_PRODUCTS + 1) for c in combinations(useful, n)]
    return min(combos, key=rank) if combos else ()


def route(*, element_keys: Iterable[str], terms: Iterable[str], catalogue: Sequence[CatalogueProduct],
          in_domains: set[str]) -> Reuse | Compose | CreateProduct | RaiseRequirement:
    """in_domains: the requested elements that are already in the domains (DomainCoverage)."""
    asked, ticked_terms = set(element_keys), sorted(set(terms))
    if not asked and not ticked_terms:
        raise NothingToRequest("Tick at least one element or term")
    cover = _best_cover(asked, catalogue)
    covered_by = [
        ProductAccess(code=p.code, name=p.name, covers=sorted(asked & set(p.element_keys)), entitled=p.entitled,
                      route=p.route)
        for p in sorted(cover, key=lambda p: p.code)
    ]
    rest = sorted(asked - set().union(*(p.element_keys for p in cover)))
    rest_in_domains = [k for k in rest if k in in_domains]

    if ticked_terms:
        return RaiseRequirement(elements=rest, terms=ticked_terms, in_domains=rest_in_domains, covered_by=covered_by)
    if not rest and len(covered_by) == 1:
        return Reuse(product=covered_by[0])
    if not rest:
        return Compose(products=covered_by)
    if len(rest_in_domains) == len(rest):
        return CreateProduct(elements=rest, covered_by=covered_by)
    return RaiseRequirement(elements=rest, terms=[], in_domains=rest_in_domains, covered_by=covered_by)


def route_request(session: Session, *, element_keys: Sequence[str], terms: Sequence[str], user_id: str,
                  roles: Sequence[str], display_name: Callable[[str], str]):
    """route() on the database: every product with its elements and the requester's access, and the
    requested elements already in the domains."""
    products = ProductRepository(session).list_with_elements()
    events = ProductGrantRepository(session).events_by_product(p.id for p in products)
    catalogue = []
    for p in products:
        access = decide(visibility=p.visibility, owner_id=p.owner_id, user_id=user_id, roles=roles,
                        events=events.get(p.id, []))
        catalogue.append(CatalogueProduct(
            code=p.code, name=p.name, element_keys=p.element_keys, entitled=access.entitled,
            route=None if access.entitled else route_to_access(display_name(p.owner_id), p.name),
        ))
    return route(element_keys=element_keys, terms=terms, catalogue=catalogue,
                 in_domains=DomainCoverage(session).in_domains(element_keys))
