"""Step 17 Discover: search the products by element, domain or words of their name and description.

Deterministic code, no model call (decision 1; the DiscoveryAssistant is phase 3). Read only.

Outcomes, one per product (CLAUDE.md): found; not_entitled_visible (shown, with the route to access).
Restricted products are shown to everyone. No hits is an empty list, not an outcome (decision 14).

Which products are hits (phase 2 plan, assumption 14):
1. a product holding a matched or candidate element, or whose name or description contains a word of
   an unmatched term as a whole word;
2. if the query names domains, only products in those domains; a query of domain words only lists
   every product in those domains;
3. ordered by the number of matched elements held (most first), then name, then code.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.entitlement.decide import decide, route_to_access
from core.repositories.glossary import GlossaryRepository
from core.repositories.grants import ProductGrantRepository
from core.repositories.products import ProductRepository
from core.steps.matching import Match, build_phrases, match, words
from core.steps.result import StepResult


class ProductSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    description: str
    domain: str
    element_keys: list[str]


class Found(StepResult):
    outcome: Literal["found"] = "found"
    product: ProductSummary


class NotEntitledVisible(StepResult):
    outcome: Literal["not_entitled_visible"] = "not_entitled_visible"
    product: ProductSummary
    route: str  # "Ask <owner> for access to <product>"


Hit = Annotated[Found | NotEntitledVisible, Field(discriminator="outcome")]


class Discovery(BaseModel):
    """What the query matched, and one typed result per product found."""

    model_config = ConfigDict(frozen=True)

    match: Match
    hits: list[Hit]
    element_names: dict[str, str]  # glossary name of every matched and candidate element, for the tick boxes


def discover(session: Session, *, query: str, user_id: str, roles: Sequence[str],
             display_name: Callable[[str], str]) -> Discovery:
    glossary = GlossaryRepository(session).list_current()
    found = match(query, build_phrases(glossary))
    matched = {e.key for e in found.elements}
    wanted = matched | {key for a in found.ambiguous for key in a.candidates}
    element_names = {e.key: e.name for e in glossary if e.key in wanted}
    term_words = {w for term in found.terms for w in words(term)}
    domains_only = bool(found.domains) and not (wanted or term_words)

    products = []
    for product in ProductRepository(session).list_with_elements():
        if found.domains and product.domain not in found.domains:
            continue
        text_words = set(words(product.name)) | set(words(product.description))
        if domains_only or wanted & set(product.element_keys) or term_words & text_words:
            products.append(product)
    products.sort(key=lambda p: (-len(matched & set(p.element_keys)), p.name, p.code))

    events = ProductGrantRepository(session).events_by_product(p.id for p in products)
    hits: list[Found | NotEntitledVisible] = []
    for product in products:
        summary = ProductSummary(code=product.code, name=product.name, description=product.description,
                                 domain=product.domain, element_keys=product.element_keys)
        access = decide(visibility=product.visibility, owner_id=product.owner_id, user_id=user_id, roles=roles,
                        events=events.get(product.id, []))
        if access.entitled:
            hits.append(Found(product=summary))
        else:
            hits.append(NotEntitledVisible(product=summary,
                                           route=route_to_access(display_name(product.owner_id), product.name)))
    return Discovery(match=found, hits=hits, element_names=element_names)
