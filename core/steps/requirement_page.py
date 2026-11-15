"""What the requester sees about one raised requirement: status, message, scope, sources, choices and the
next action.

Read only. The requester's messages are kept here, in one place (phase 3 plan, assumptions 5, 9, 14, 32,
34 and 55). The onboarding message is CLAUDE.md's, word for word.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.domain.requirement import Requirement
from core.domain.sourcing import FetchOutcome, RequirementScope, SourceChoice, SourceSummary
from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirement_scopes import RequirementScopeRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.request_data import RAISED
from core.steps.s1_scope import ScopeProposed, proposed_scope
from core.steps.s2_sources import SourceCandidate, listed_sources
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

NextAction = Literal["find_source", "confirm_scope", "ask_for_scope", "list_sources", "pick_source"]


def source_down_message(source_name: str) -> str:
    return f"A source was found: {source_name}. It did not answer; Amaze tries again every day."


def source_selected_message(source_name: str) -> str:
    return f"A source was found: {source_name}. Amaze is fetching its data."


def approval_missing_message(source_name: str) -> str:
    return f"{source_name} can be used only under these conditions. Accept them to continue."


def released_message(source_name: str, reason: str, release_days: int, *, source_left: bool) -> str:
    """Assumption 55, word for word."""
    if reason == "source_down_n_days":
        head = f"{source_name} did not answer for {release_days} days in a row."
    else:
        head = f"You released {source_name}."
    tail = "Pick another source." if source_left else \
        "No other source is listed. Add a note and ask for a new scope."
    return f"{head} {tail}"


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


def _name(catalogue: SourceCatalogue, code: str) -> str:
    try:
        return catalogue.get(code).name
    except UnknownSource:  # the catalogue changed since the choice
        return code


def _newer(choice: SourceChoice | None, scope: RequirementScope | None) -> bool:
    """The current choice is a release made after the confirmed scope (or with no scope at all)."""
    return choice is not None and choice.action == "released" and (scope is None or scope.created_at < choice.created_at)


def requirement_page(session: Session, requirement: Requirement, catalogue: SourceCatalogue,
                     release_days: int = 3) -> RequirementPage:
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

    status = requirement.status
    next_action: NextAction | None = None
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
        message = released_message(source_name, current.reason, release_days, source_left=True) \
            if _newer(current, scope) else PICK_A_SOURCE
    elif status == "no_source_found":
        next_action = "ask_for_scope"
        if _newer(current, scope):
            message = released_message(source_name, current.reason, release_days, source_left=False)
        elif scope is not None and any(p.created_at > scope.created_at for p in
                                       ProposalRepository(session).list_for_requirement(requirement.id, "source_list")):
            message = ALL_DECLINED
        else:
            message = NO_FIT
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
    )
