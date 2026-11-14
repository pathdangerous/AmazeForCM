"""What the requester sees about one raised requirement: status, message, source, last fetch, next action.

Read only. The requester's messages are kept here, in one place (phase 3 plan, assumptions 5, 9 and 14);
3b adds its own statuses. The onboarding message is CLAUDE.md's, word for word.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.domain.requirement import Requirement
from core.domain.sourcing import FetchOutcome, SourceSummary
from core.repositories.feeds import FeedRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.request_data import RAISED
from ports.connectors.catalogue import SourceCatalogue, UnknownSource

ONBOARDING = "Your data is getting onboarded; we will notify you once it is ready."
NO_KNOWN_SOURCE = "No known source has this data yet."


def source_down_message(source_name: str) -> str:
    return f"A source was found: {source_name}. It did not answer; Amaze tries again every day."


def source_selected_message(source_name: str) -> str:
    return f"A source was found: {source_name}. Amaze is fetching its data."


class SourceOnPage(SourceSummary):
    feed_code: str


class LastFetch(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: FetchOutcome
    at: datetime
    detail: str | None


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
    source: SourceOnPage | None
    last_fetch: LastFetch | None
    next_action: Literal["find_source"] | None


def requirement_page(session: Session, requirement: Requirement, catalogue: SourceCatalogue) -> RequirementPage:
    source = last = None
    choice = SourceChoiceRepository(session).current(requirement.id)
    if choice is not None and choice.action != "selected":
        choice = None  # released: no source is selected now (task 17 adds the release messages)
    if choice is not None:
        feed = FeedRepository(session).get(choice.feed_id)
        try:
            summary = SourceSummary.of(catalogue.get(choice.source_code))
        except UnknownSource:  # the catalogue changed since the choice: show the feed's own name
            summary = None
        if summary is not None:
            source = SourceOnPage(**summary.model_dump(), feed_code=feed.code)
        fetch = SourceFetchRepository(session).latest(feed.id)
        if fetch is not None:
            last = LastFetch(outcome=fetch.outcome, at=fetch.created_at, detail=fetch.detail)
    source_name = source.name if source else (choice.source_code if choice else "")

    if requirement.status == "defined":
        message = RAISED
    elif requirement.status == "onboarding":
        message = ONBOARDING
    elif requirement.status == "source_selected":
        message = source_down_message(source_name) if last and last.outcome == "source_down" \
            else source_selected_message(source_name)
    else:  # statuses of later phases
        message = f"Status: {requirement.status.replace('_', ' ')}."
    return RequirementPage(
        id=requirement.id, created_at=requirement.created_at, title=requirement.title,
        description=requirement.description, status=requirement.status, message=message,
        elements=requirement.elements, terms=requirement.terms, source=source, last_fetch=last,
        next_action="find_source" if requirement.status == "defined" else None,
    )
