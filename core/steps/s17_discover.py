"""Step 17 Discover: search the products by element, domain or words of their name and description.

discover() is deterministic code, no model call (decision 1). Read only. Phase 3b adds assist(): an
optional, separate call to the read-only DiscoveryAssistant, received as an argument (assumption 22).

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
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.sourcing import AgentRun, AssistantInput, GlossaryRef
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
    return Discovery(match=found, hits=_hits(session, products, user_id, roles, display_name),
                     element_names=element_names)


def _hits(session: Session, products, user_id: str, roles: Sequence[str],
          display_name: Callable[[str], str]) -> list[Found | NotEntitledVisible]:
    """One typed result per product; entitlement is decided by core/entitlement, never by an agent."""
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
    return hits


# ---- phase 3b: the DiscoveryAssistant beside the search (phase 3 plan, assumptions 38 to 40)


class RunsAgent(Protocol):
    def run(self, session: Session, agent_input: BaseModel, *, before_write=None) -> AgentRun: ...


class Suggestion(BaseModel):
    """A glossary element the assistant thinks the requester means, shown unticked as "suggested"."""

    model_config = ConfigDict(frozen=True)

    term: str
    element_key: str
    element_name: str
    reason: str
    products: list[Hit]  # the products holding it, with entitlement as in the search


class Assistance(BaseModel):
    model_config = ConfigDict(frozen=True)

    log_id: UUID | None  # None when there was nothing to ask
    suggestions: list[Suggestion]


def assist_input(session: Session, *, query: str) -> AssistantInput | None:
    """Read only: the DiscoveryAssistant's input, or None when the search left no word unmatched (no
    model call then). The caller ends the read transaction before the model call (review fix 2)."""
    glossary = GlossaryRepository(session).list_current()
    unmatched = match(query, build_phrases(glossary)).terms if query.strip() else []
    if not unmatched:
        return None
    return AssistantInput(query=query, unmatched_terms=unmatched,
                          glossary=[GlossaryRef(key=e.key, name=e.name, aliases=e.aliases) for e in glossary])


def record_assist(session: Session, *, agent: RunsAgent, agent_input: AssistantInput | None, user_id: str,
                  roles: Sequence[str], display_name: Callable[[str], str]) -> Assistance:
    """The model call, then the log row and the suggestions. Code keeps only suggestions for the
    unmatched words and for keys in the glossary, each once. Writes nothing but the agent's proposal_log
    row; the caller commits it."""
    if agent_input is None:
        return Assistance(log_id=None, suggestions=[])
    run = agent.run(session, agent_input)
    unmatched = agent_input.unmatched_terms
    names = {e.key: e.name for e in GlossaryRepository(session).list_current()}
    products = ProductRepository(session).list_with_elements()
    seen, suggestions = set(), []
    for suggested in run.output.suggestions:
        pair = (suggested.term, suggested.element_key)
        if suggested.term not in unmatched or suggested.element_key not in names or pair in seen:
            continue
        seen.add(pair)
        holding = sorted((p for p in products if suggested.element_key in p.element_keys),
                         key=lambda p: (p.name, p.code))
        suggestions.append(Suggestion(term=suggested.term, element_key=suggested.element_key,
                                      element_name=names[suggested.element_key], reason=suggested.reason,
                                      products=_hits(session, holding, user_id, roles, display_name)))
    return Assistance(log_id=run.log_id, suggestions=suggestions)


def assist(session: Session, *, query: str, agent: RunsAgent, user_id: str, roles: Sequence[str],
           display_name: Callable[[str], str]) -> Assistance:
    """assist_input() then record_assist() on one session. The API ends the read transaction between them."""
    return record_assist(session, agent=agent, agent_input=assist_input(session, query=query), user_id=user_id,
                         roles=roles, display_name=display_name)
