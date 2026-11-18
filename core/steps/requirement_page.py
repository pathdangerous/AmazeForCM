"""What the requester sees about one raised requirement: status, message, scope, sources, choices and the
next action.

Read only. The requester's messages are kept here, in one place (phase 3 plan, assumptions 5, 9, 14, 32,
34 and 55). The onboarding message is CLAUDE.md's, word for word.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.domain.requirement import Requirement
from core.domain.sourcing import FetchOutcome, RequirementScope, SourceChoice, SourceSummary
from core.entitlement.decide import route_to_access
from core.repositories.certifications import LoadCertificationRepository
from core.repositories.feed_signoffs import FeedSignoffRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.go_lives import GoLiveRepository
from core.repositories.loads import LoadRepository
from core.repositories.products import ProductRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirement_scopes import RequirementScopeRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.request_data import RAISED
from core.steps.s1_scope import ScopeProposed, proposed_scope
from core.steps.s2_sources import SourceCandidate, listed_sources
from core.steps.s20_create_product import uncovered
from ports.connectors.catalogue import SourceCatalogue, UnknownSource

ONBOARDING = "Your data is getting onboarded; we will notify you once it is ready."
NO_KNOWN_SOURCE = "No known source has this data yet."
SCOPE_TO_CONFIRM = "No known source has this data yet. Confirm where Amaze should look for it."
# Approved at checkpoint 5 (2026-11-15), word for word.
SCOPE_NOT_CONFIRMED = "You didn't confirm where to look. Add a note and ask again."
SCOPE_CONFIRMED = "Thanks. Amaze is now finding sources for this data."
PICK_A_SOURCE = "Pick a source and accept its conditions to start onboarding."
NO_FIT = "No source fits this request. The backend team has been told."
ALL_DECLINED = "You declined every listed source. Add a note and ask Amaze to look again."
# Independent review, fix 7: every listed source left the catalogue. Wording not yet approved.
NO_SOURCE_LEFT = "The listed sources are no longer offered. Add a note and ask for a new scope."

NextAction = Literal["find_source", "confirm_scope", "ask_for_scope", "list_sources", "pick_source"]


def source_down_message(source_name: str) -> str:
    return f"A source was found: {source_name}. It did not answer; Amaze tries again every day."


def source_selected_message(source_name: str) -> str:
    return f"A source was found: {source_name}. Amaze is fetching its data."


def approval_missing_message(source_name: str) -> str:
    return f"{source_name} can be used only under these conditions. Accept them to continue."


def accepted_with_gaps_message(source_name: str, gaps: list[str]) -> str:
    """Phase 4 plan, assumption 26, approved word for word; shown under the onboarding message."""
    return f"The backend team accepted {source_name} with gaps: {'; '.join(gaps)}."


def rejected_message(source_name: str, reasons: list[str], *, source_left: bool) -> str:
    """Phase 4 plan, assumption 26, approved word for word."""
    tail = "Pick another source." if source_left else \
        "No other source is listed. Add a note and ask for a new scope."
    return f"The backend team rejected {source_name}: {'; '.join(reasons)}. {tail}"


def released_message(source_name: str, reason: str, release_days: int, *, source_left: bool) -> str:
    """Assumption 55, word for word."""
    if reason == "source_down_n_days":
        head = f"{source_name} did not answer for {release_days} days in a row."
    else:
        head = f"You released {source_name}."
    tail = "Pick another source." if source_left else \
        "No other source is listed. Add a note and ask for a new scope."
    return f"{head} {tail}"


ProgressState = Literal["checking_file", "setting_up", "approved", "delivery_held", "part_missing"]


def progress_text(state: ProgressState, source_name: str, *, reason: str = "", elements: str = "") -> str:
    """Phase 7 assumption 33 (approved 2026-11-17, without "Step N of 4"); shown under the onboarding message."""
    return {
        "checking_file": f"The backend team is checking the first file from {source_name}.",
        "setting_up": f"{source_name} is registered; the backend team is setting up how its data is read and checked.",
        "approved": f"The data owner approved {source_name} for loading; the first delivery is being checked.",
        "delivery_held": f"The latest delivery from {source_name} was held ({reason}); the next delivery is "
                         "checked again.",
        "part_missing": f"{source_name} is released, but it does not hold {elements} yet; the backend team has "
                        "been told.",
    }[state]


def ready_message(product_name: str) -> str:
    return f"Ready. Your data is in {product_name}."


def access_restricted_message(product_name: str, route: str) -> str:
    return f"Access restricted. Your data is in {product_name}. {route}."


def held_note(held_on: str, reason: str, released_on: str) -> str:
    return f"The delivery of {held_on} was held ({reason}); you still see the data released on {released_on}."


class Progress(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: ProgressState
    text: str


class ProductOnPage(BaseModel):
    """The product that holds the requester's data (assumption 35). entitled is what step 20 decided."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    entitled: bool
    route: str | None


class SourceOnPage(SourceSummary):
    feed_code: str


class LastFetch(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: FetchOutcome
    at: datetime
    detail: str | None


class ChoiceOnPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    action: str
    source_code: str
    source_name: str
    reason: str | None
    at: datetime


class RequirementPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    created_at: datetime
    title: str
    description: str
    status: str
    message: str
    elements: list[str]
    terms: list[str]
    source: SourceOnPage | None  # the source selected now; None after a release
    last_fetch: LastFetch | None
    next_action: NextAction | None
    proposed_scope: ScopeProposed | None  # waiting for the requester to confirm
    scope: RequirementScope | None  # the confirmed one
    listed: list[SourceCandidate]  # sources the requester can pick now
    choices: list[ChoiceOnPage]  # every choice and release, oldest first
    can_release: bool
    # Phase 4: the gaps of an accepting sign-off of the selected feed, shown under the onboarding message.
    signoff_note: str | None = None
    # Phase 7: where the data is while onboarding; the product once ready or restricted; a later held delivery.
    progress: Progress | None = None
    product: ProductOnPage | None = None
    held_note: str | None = None
    # Phase 7.5, P1: each element key with its current glossary name (a key the glossary lost shows as itself).
    element_names: dict[str, str] = {}


def _held_reason(session: Session, load) -> str:
    """Why a scheduled load was held: step 12's reason, or step 15's score against the threshold."""
    if load.arrival_outcome == "breaking_held":
        return (load.arrival_detail or {}).get("reason") or "it did not match the structure of the feed"
    certification = LoadCertificationRepository(session).for_load(load.id)
    if certification is not None:
        return f"its trust score of {certification.trust_score} is below {certification.threshold}"
    return load.failure or "it did not pass its checks"


def _feed_progress(session: Session, requirement: Requirement, feed, source_name: str) -> Progress | None:
    """Assumption 33: one line for an onboarding requirement; None when nothing is left to say."""
    def of(state: ProgressState, **kw: str) -> Progress:
        return Progress(state=state, text=progress_text(state, source_name, **kw))

    if feed.stage == "candidate":
        return of("checking_file")
    product = ProductRepository(session).for_feed(feed.id)
    if product is not None:
        missing, outstanding = uncovered(session, ProductRepository(session).elements_of(product.id),
                                         requirement.elements, requirement.terms)
        if not (missing or outstanding):
            return None  # linked after the release: it becomes ready at the next one (assumption 29)
        glossary = GlossaryRepository(session)
        names = [glossary.get_current(key).name for key in missing] + outstanding
        return of("part_missing", elements=", ".join(names))
    scheduled = [load for load in LoadRepository(session).list_for_feed(feed.id) if load.purpose == "scheduled"]
    if scheduled and scheduled[0].status == "held":
        return of("delivery_held", reason=_held_reason(session, scheduled[0]))
    if GoLiveRepository(session).latest(feed.id) is not None:
        return of("approved")
    return of("setting_up")


def _product_and_note(session: Session, requirement: Requirement, feed_id: UUID, display_name):
    """The product of a ready or access_restricted requirement, and the note about a held later delivery."""
    product = ProductRepository(session).for_feed(feed_id)
    if product is None:
        return None, None
    ready = requirement.status == "ready"
    route = None if ready else route_to_access(display_name(product.owner_id), product.name)
    scheduled = [load for load in LoadRepository(session).list_for_feed(feed_id) if load.purpose == "scheduled"]
    released = next((load for load in scheduled if load.status == "released"), None)
    note = None
    if scheduled and scheduled[0].status == "held" and released is not None:
        note = held_note(f"{scheduled[0].received_at:%Y-%m-%d}", _held_reason(session, scheduled[0]),
                         f"{released.received_at:%Y-%m-%d}")
    return ProductOnPage(code=product.code, name=product.name, entitled=ready, route=route), note


def _name(catalogue: SourceCatalogue, code: str) -> str:
    try:
        return catalogue.get(code).name
    except UnknownSource:  # the catalogue changed since the choice
        return code


def _newer(choice: SourceChoice | None, scope: RequirementScope | None) -> bool:
    """The current choice is a release made after the confirmed scope (or with no scope at all)."""
    return choice is not None and choice.action == "released" and (scope is None or scope.created_at < choice.created_at)


def requirement_page(session: Session, requirement: Requirement, catalogue: SourceCatalogue,
                     release_days: int = 3, display_name: Callable[[str], str] = lambda user_id: user_id
                     ) -> RequirementPage:
    choices = SourceChoiceRepository(session)
    history = choices.history(requirement.id)
    current = history[-1] if history else None
    scope = RequirementScopeRepository(session).current(requirement.id)
    listed = listed_sources(session, requirement.id, catalogue) \
        if requirement.status == "awaiting_source_choice" else []

    source = last = None
    if current is not None and current.action == "selected":
        feed = FeedRepository(session).get(current.feed_id)
        try:
            source = SourceOnPage(**SourceSummary.of(catalogue.get(current.source_code)).model_dump(),
                                  feed_code=feed.code)
        except UnknownSource:
            source = None
        fetch = SourceFetchRepository(session).latest(feed.id)
        if fetch is not None:
            last = LastFetch(outcome=fetch.outcome, at=fetch.created_at, detail=fetch.detail)
    source_name = _name(catalogue, current.source_code) if current else ""
    signoff = FeedSignoffRepository(session).latest(current.feed_id) if current else None

    def after_release(source_left: bool) -> str:
        if current.reason == "candidate_rejected":
            return rejected_message(source_name, signoff.reasons, source_left=source_left)
        return released_message(source_name, current.reason, release_days, source_left=source_left)

    status = requirement.status
    next_action: NextAction | None = None
    progress = product = note = None
    selected = current is not None and current.action == "selected"
    if status == "onboarding" and selected:
        progress = _feed_progress(session, requirement, FeedRepository(session).get(current.feed_id), source_name)
    if status in ("ready", "access_restricted") and selected:
        product, note = _product_and_note(session, requirement, current.feed_id, display_name)
    if status == "defined":
        message, next_action = RAISED, "find_source"
    elif status == "onboarding":
        message = ONBOARDING
    elif status == "source_selected":
        message = source_down_message(source_name) if last and last.outcome == "source_down" \
            else source_selected_message(source_name)
    elif status == "awaiting_scope_confirmation":
        message, next_action = SCOPE_TO_CONFIRM, "confirm_scope"
    elif status == "scope_not_confirmed":
        message, next_action = SCOPE_NOT_CONFIRMED, "ask_for_scope"
    elif status == "scope_confirmed":
        message, next_action = SCOPE_CONFIRMED, "list_sources"
    elif status == "awaiting_source_choice":
        next_action = "pick_source"
        message = after_release(True) if _newer(current, scope) else PICK_A_SOURCE
    elif status == "no_source_found":
        next_action = "ask_for_scope"
        listed_now = [] if scope is None else [
            p for p in ProposalRepository(session).list_for_requirement(requirement.id, "source_list")
            if p.created_at > scope.created_at]
        if _newer(current, scope):
            message = after_release(False)
        elif any(p.status == "pending" for p in listed_now):  # undecided, so they left the catalogue
            message = NO_SOURCE_LEFT
        elif listed_now:
            message = ALL_DECLINED
        else:
            message = NO_FIT
    elif status == "ready" and product is not None:
        message = ready_message(product.name)
    elif status == "access_restricted" and product is not None:
        message = access_restricted_message(product.name, product.route)
    else:  # statuses of later phases
        message = f"Status: {status.replace('_', ' ')}."

    return RequirementPage(
        id=requirement.id, created_at=requirement.created_at, title=requirement.title,
        description=requirement.description, status=status, message=message, elements=requirement.elements,
        terms=requirement.terms, source=source, last_fetch=last, next_action=next_action,
        proposed_scope=proposed_scope(session, requirement.id) if status == "awaiting_scope_confirmation" else None,
        scope=scope, listed=listed,
        choices=[ChoiceOnPage(version=c.version, action=c.action, source_code=c.source_code,
                              source_name=_name(catalogue, c.source_code), reason=c.reason, at=c.created_at)
                 for c in history],
        can_release=status == "source_selected",
        signoff_note=accepted_with_gaps_message(source_name, signoff.gaps)
        if status == "onboarding" and current.action == "selected" and signoff and signoff.outcome == "meets_with_gaps"
        else None,
        progress=progress, product=product, held_note=note,
        element_names=_element_names(session, requirement.elements),
    )


def _element_names(session: Session, keys: list[str]) -> dict[str, str]:
    names = {e.key: e.name for e in GlossaryRepository(session).list_current()}
    return {key: names.get(key, key) for key in keys}
