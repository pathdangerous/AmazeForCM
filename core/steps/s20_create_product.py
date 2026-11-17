"""Step 20, create product (phase 7 plan, assumptions 19, 24 to 29), and the one finishing transaction of a scheduled
load (assumption 19).

Outcomes (CLAUDE.md): created, data_not_in_domains. Rules execute: no agent, no model call. Not an outcome: a load
that is held (step 15), where step 20 does not run.

On a release the feed has one product, created at its first release: the feed's code and name, the domain most of
its elements are in, its current mappings' elements, and the owner and visibility of the latest registration
(phase 4 note 5; G15). Each `onboarding` requirement that selects the feed is checked at every release:
- every element it asks for is in the product, and every ticked term is covered by an element's name or alias
  -> created: the requirement becomes `ready`, or `access_restricted` when the product is restricted and the
  requester has no grant (core/entitlement/decide.py), and the requester is told ("Ready: ..." or "Access
  restricted: ..." with the route);
- otherwise -> data_not_in_domains: it stays `onboarding` and the backend team is told what is missing.
No grant is written (assumption 26).

finish_scheduled_load() locks the feed row, the requirements that select the feed in id order, then (in
record_load) the load row, and runs steps 15 and 20 inside the engine's savepoint, so that the records, the
certification, the product and the requirement moves commit together or the load fails naming the step that was
refused. The step flushes; the caller commits.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from core.domain.load import Load
from core.engine.run import AfterLoadRefused, LoadOutcome, LoadPlan, LoadRun, record_load
from core.entitlement.decide import decide, route_to_access
from core.repositories.configuration import MappingRepository
from core.repositories.errors import RepositoryError
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.grants import ProductGrantRepository
from core.repositories.products import ProductRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.steps.candidates import phrases
from core.steps.matching import words
from core.steps.result import StepResult
from core.steps.s15_certify import HeldConsumersWarned, Released, certify
from ports.notify.base import Notifier


class Created(StepResult):
    outcome: Literal["created"] = "created"
    requirement_id: UUID
    product_code: str
    product_new: bool  # this release created the product
    status: Literal["ready", "access_restricted"]


class DataNotInDomains(StepResult):
    outcome: Literal["data_not_in_domains"] = "data_not_in_domains"
    requirement_id: UUID
    missing: list[str]  # element keys the product does not hold
    terms: list[str]  # ticked terms no element of the product covers


Step20Result = Annotated[Created | DataNotInDomains, Field(discriminator="outcome")]


@dataclass(frozen=True)
class FinishContext:
    """What steps 15 and 20 need from the caller: the notifier, the threshold (configuration) and the people."""

    notifier: Notifier
    threshold: Decimal
    operations: Sequence[str] = ()
    backend_team: Sequence[str] = ()
    roles_of: Callable[[str], Sequence[str]] = field(default=lambda user_id: ())
    display_name: Callable[[str], str] = field(default=lambda user_id: user_id)


class FinishedLoad(BaseModel):
    """A scheduled load, written: the engine's run, step 15's result (None if the load failed) and, when it was
    released, the product's code and each waiting requirement's step 20 outcome."""

    model_config = ConfigDict(frozen=True)

    run: LoadRun
    certification: Released | HeldConsumersWarned | None
    product_code: str | None
    requirements: list[Step20Result]


def finish_scheduled_load(session: Session, plan: LoadPlan, outcome: LoadOutcome, *,
                          context: FinishContext) -> FinishedLoad:
    """Lock order (assumption 17): the feed row, the requirements selecting it in id order, the load row (in
    record_load). Steps 15 and 20 run inside the engine's savepoint."""
    feed_id = plan.load.feed_id
    FeedRepository(session).lock(feed_id)
    requirements = RequirementRepository(session)
    for requirement_id in sorted(SourceChoiceRepository(session).requirements_selecting(feed_id)):
        requirements.lock(requirement_id)
    done: dict = {}

    def then(inner: Session, load: Load) -> None:
        done["certification"] = certify(inner, load, threshold=context.threshold, notifier=context.notifier,
                                        operations=context.operations, backend_team=context.backend_team)
        if isinstance(done["certification"], Released):
            done["product_code"], done["requirements"] = create_product(inner, load, context=context)

    run = record_load(session, plan, outcome, then=then)
    if run.load.status not in ("released", "held"):  # failed: the savepoint was rolled back with its writes
        done.clear()
    return FinishedLoad(run=run, certification=done.get("certification"), product_code=done.get("product_code"),
                        requirements=done.get("requirements", []))


def create_product(session: Session, load: Load, *, context: FinishContext) -> tuple[str, list[Step20Result]]:
    """Step 20 for a released load. AfterLoadRefused('create_product', ...) if the product or a move is refused."""
    try:
        return _create_product(session, load, context)
    except DBAPIError as exc:
        raise AfterLoadRefused("create_product", str(exc.orig).splitlines()[0]) from exc
    except RepositoryError as exc:
        raise AfterLoadRefused("create_product", str(exc)) from exc


def _create_product(session: Session, load: Load, context: FinishContext) -> tuple[str, list[Step20Result]]:
    feed_id = load.feed_id
    requirements = RequirementRepository(session)
    waiting = [r for r in (requirements.get(i) for i in SourceChoiceRepository(session).requirements_selecting(feed_id))
               if r.status == "onboarding"]
    products = ProductRepository(session)
    product, new = products.for_feed(feed_id), False
    if product is None:
        product, new = _new_product(session, feed_id, waiting[0].id if waiting else None), True
    held = products.elements_of(product.id)
    glossary = GlossaryRepository(session)
    named = {p for key in held for p in phrases(glossary.get_current(key))}
    results: list[Step20Result] = []
    for requirement in waiting:
        missing = [key for key in requirement.elements if key not in held]
        outstanding = [term for term in requirement.terms if words(term) not in named]
        if missing or outstanding:
            results.append(DataNotInDomains(requirement_id=requirement.id, missing=missing, terms=outstanding))
            _tell_backend(context, product.name, requirement.title, missing, outstanding)
            continue
        access = decide(visibility=product.visibility, owner_id=product.owner_id, user_id=requirement.requester_id,
                        roles=list(context.roles_of(requirement.requester_id)),
                        events=ProductGrantRepository(session).events_for(product.id))
        status = "ready" if access.entitled else "access_restricted"
        requirements.advance(requirement.id, status)
        if access.entitled:
            subject, body = f"Ready: {requirement.title}", f"Your data is in {product.name}."
        else:
            route = route_to_access(context.display_name(product.owner_id), product.name)
            subject, body = f"Access restricted: {requirement.title}", f"Your data is in {product.name}. {route}."
        context.notifier.notify(recipient=requirement.requester_id, subject=subject, body=body)
        results.append(Created(requirement_id=requirement.id, product_code=product.code, product_new=new,
                               status=status))
    return product.code, results


def _new_product(session: Session, feed_id: UUID, requirement_id: UUID | None):
    """Assumption 24: the first release creates the feed's product."""
    feed = FeedRepository(session).get(feed_id)
    registration = FeedRegistrationRepository(session).current(feed_id)
    keys = sorted({m.element_key for m in MappingRepository(session).current_for_feed(feed_id)})
    glossary = GlossaryRepository(session)
    domains = Counter(glossary.get_current(key).domain for key in keys)
    domain = min(domains, key=lambda d: (-domains[d], d))  # the most frequent; ties alphabetical
    return ProductRepository(session).create(
        code=feed.code, name=feed.name, domain=domain, description=f"Released from the {feed.name} feed.",
        requirement_id=requirement_id, feed_id=feed_id, owner_id=registration.owner_id,
        visibility=registration.visibility, element_keys=keys)


def _tell_backend(context: FinishContext, product: str, title: str, missing: Sequence[str],
                  outstanding: Sequence[str]) -> None:
    parts = []
    if missing:
        parts.append(f"elements {', '.join(missing)}")
    if outstanding:
        parts.append(f"terms {', '.join(outstanding)}")
    for person in context.backend_team:
        context.notifier.notify(
            recipient=person, subject=f"Requirement not covered: {title}",
            body=f"{product} was released, but it does not hold {' and '.join(parts)} that the requirement "
                 "asks for. It stays onboarding.")
