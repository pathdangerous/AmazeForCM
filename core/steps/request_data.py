"""request_data: one "Request this data", from the ticked elements and terms to the requester's message.

In order (phase 2 plan, assumption 3):
1. check the input: clean the terms, refuse a term that is a glossary phrase, refuse unknown keys;
2. run the routing checks;
3. run step 01 when the outcome is raise_requirement;
4. for create_product, tell the backend team;
5. record the consumer_requests row;
6. return the typed results, the message and the notes.

Everything is flushed inside the caller's transaction, never committed (assumption 4).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.repositories.consumer_requests import ConsumerRequestRepository
from core.repositories.glossary import GlossaryRepository
from core.steps.errors import NothingToRequest, TermInGlossary
from core.steps.matching import build_phrases, words
from core.steps.routing import Compose, CreateProduct, ProductAccess, RaiseRequirement, Reuse, RoutingResult, route_request
from core.steps.s01_define_requirement import DefineRequirementResult, define_requirement, describe
from ports.notify.base import Notifier

RAISED = "Requirement raised; the backend team will look for a source."
CREATE = "This data is already in Amaze. The backend team has been asked to create a product."


class RequestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    routing: RoutingResult
    requirement: DefineRequirementResult | None  # step 01, when a requirement was raised
    message: str
    notes: list[str]
    notified: list[str]  # every user id told about this request


def _names(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _message(routing: Reuse | Compose | CreateProduct | RaiseRequirement) -> str:
    if isinstance(routing, Reuse):
        p = routing.product
        return f"This data is available in {p.name}." if p.entitled else (
            f"This data is in {p.name}. Access restricted. {p.route}."
        )
    if isinstance(routing, Compose):
        return (f"This data can be composed from {_names([p.name for p in routing.products])}. "
                "Composition is not available in the PoC yet.")
    return CREATE if isinstance(routing, CreateProduct) else RAISED


def _products(routing) -> list[ProductAccess]:
    if isinstance(routing, Reuse):
        return [routing.product]
    return routing.products if isinstance(routing, Compose) else routing.covered_by


def request_data(session: Session, *, requester_id: str, roles: Sequence[str], query: str, purpose: str,
                 element_keys: Sequence[str], terms: Sequence[str], notifier: Notifier,
                 recipients: Mapping[str, Sequence[str]], display_name: Callable[[str], str]) -> RequestResult:
    glossary = GlossaryRepository(session)
    current = glossary.list_current()
    phrases = build_phrases(current)
    element_names = {e.key: e.name for e in current}

    keys = list(dict.fromkeys(element_keys))
    glossary.require_keys(keys)  # raises UnknownGlossaryElement
    cleaned = list(dict.fromkeys(" ".join(words(t)) for t in terms if words(t)))
    in_glossary = [t for t in cleaned if phrases.is_phrase(t)]
    if in_glossary:
        raise TermInGlossary(f"Already in the glossary, tick the element instead: {', '.join(in_glossary)}")
    if not keys and not cleaned:
        raise NothingToRequest("Tick at least one element or term")

    routing = route_request(session, element_keys=keys, terms=cleaned, user_id=requester_id, roles=roles,
                            display_name=display_name)
    requirement = None
    notified: list[str] = []
    if isinstance(routing, RaiseRequirement):
        requirement = define_requirement(
            session, requester_id=requester_id, query=query, purpose=purpose, element_keys=routing.elements,
            terms=routing.terms, notifier=notifier, recipients=recipients, display_name=display_name,
        )
        notified = list(requirement.notified)
    elif isinstance(routing, CreateProduct):
        body = describe(display_name(requester_id), routing.elements, [])
        for user_id in recipients.get("backend_team", ()):
            notifier.notify(recipient=user_id, subject="Product requested from data already in Amaze", body=body)
            notified.append(user_id)

    products = _products(routing)
    notes = [] if isinstance(routing, Reuse) else [
        f"Already available: {', '.join(element_names.get(k, k) for k in p.covers)} in {p.name}."
        + ("" if p.entitled else f" {p.route}.")
        for p in products
    ]
    if cleaned:
        notes.append(f"Not in the glossary yet: {', '.join(sorted(cleaned))}. The glossary owner has been told.")

    message = _message(routing)
    stored = {"routing": routing.model_dump(mode="json"),
              "requirement": requirement.model_dump(mode="json") if requirement else None,
              "message": message, "notes": notes, "notified": notified}
    row = ConsumerRequestRepository(session).record(
        requester_id=requester_id, query=query, purpose=purpose, element_keys=keys, terms=cleaned,
        outcome=routing.outcome, product_codes=[p.code for p in products],
        requirement_id=requirement.requirement.id if requirement else None, result=stored,
    )
    return RequestResult(request_id=row.id, routing=routing, requirement=requirement, message=message, notes=notes,
                         notified=notified)
