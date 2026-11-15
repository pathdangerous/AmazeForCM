"""What the backend team sees about one feed (phase 4 plan, sections 9 and 10). Read only.

The source and its conditions; each requirement that selects the feed now, with the conditions its
requester accepted (note 4: where access is restricted knowingly); the latest file; the latest profile
with its undeclared and missing columns, and the history; the ProfilerAgent's summary; the sign-offs; the
registration; and the next action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.domain.feed import Feed, FeedRegistration
from core.domain.profiling import FeedProfile, FeedSignoff
from core.domain.sourcing import SourceFetch, SourceSummary
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feed_signoffs import FeedSignoffRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.s02_profile import ProfileSummary, summary_of
from ports.connectors.catalogue import SourceCatalogue, SourceConditions, UnknownSource

FeedAction = Literal["profile", "summarise", "sign_off", "register"]


class FeedRequirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str
    status: str
    requester_id: str
    basis: str | None
    accepted_conditions: SourceConditions | None  # None for a known source: Amaze holds the agreement


class ProfileOnPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    outcome: str
    at: datetime


class FeedPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    feed: Feed
    source: SourceSummary | None
    requirements: list[FeedRequirement]
    last_file: SourceFetch | None
    profile: FeedProfile | None  # the latest
    undeclared_columns: list[str]  # in the file, not declared by the spec
    missing_columns: list[str]  # declared by the spec, not in the file
    profiles: list[ProfileOnPage]
    summary: ProfileSummary | None  # of the latest profile
    summary_status: str | None
    signoffs: list[FeedSignoff]
    registration: FeedRegistration | None
    next_action: FeedAction | None


def feed_page(session: Session, feed: Feed, catalogue: SourceCatalogue) -> FeedPage:
    try:
        source = SourceSummary.of(catalogue.get(feed.source_code)) if feed.source_code else None
    except UnknownSource:
        source = None
    choices, requirements = SourceChoiceRepository(session), RequirementRepository(session)
    rows = []
    for requirement_id in choices.requirements_selecting(feed.id):
        requirement, choice = requirements.get(requirement_id), choices.current(requirement_id)
        rows.append(FeedRequirement(id=requirement.id, title=requirement.title, status=requirement.status,
                                    requester_id=requirement.requester_id, basis=choice.basis,
                                    accepted_conditions=choice.accepted_conditions))
    profiles = FeedProfileRepository(session).history(feed.id)
    latest = profiles[-1] if profiles else None
    undeclared = missing = []
    if latest is not None:
        in_file = [c.name for c in latest.columns]
        undeclared = [n for n in in_file if n not in latest.spec.columns]
        missing = [n for n in latest.spec.columns if n not in in_file] if in_file else []
    proposal = summary_of(session, feed_id=feed.id, profile_id=latest.id) if latest else None
    summary = None if proposal is None else ProfileSummary(
        proposal_id=proposal.id, log_id=proposal.proposal_log_id, profile_id=latest.id, **{
            k: proposal.payload[k] for k in ("summary", "recommended_outcome", "gaps", "reasons")})
    signoffs = FeedSignoffRepository(session).history(feed.id)
    last_file = SourceFetchRepository(session).latest(feed.id)
    has_file = SourceFetchRepository(session).latest_fetched(feed.id) is not None

    next_action: FeedAction | None = None
    if feed.stage == "candidate" and has_file:
        if latest is None:
            next_action = "profile"
        elif proposal is None:
            next_action = "summarise"
        elif proposal.status == "pending":
            next_action = "sign_off"
        elif signoffs and signoffs[-1].profile_id == latest.id and signoffs[-1].outcome != "rejected":
            next_action = "register"
    return FeedPage(
        feed=feed, source=source, requirements=rows, last_file=last_file, profile=latest,
        undeclared_columns=undeclared, missing_columns=missing,
        profiles=[ProfileOnPage(version=p.version, outcome=p.outcome, at=p.created_at) for p in profiles],
        summary=summary, summary_status=proposal.status if proposal else None, signoffs=signoffs,
        registration=FeedRegistrationRepository(session).current(feed.id), next_action=next_action)
