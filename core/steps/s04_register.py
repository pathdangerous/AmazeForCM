"""Step 04 register (phase 4 plan, assumptions 28 to 33).

Outcomes (CLAUDE.md): registered; duplicate_linked; no_owner.

- register_feed(): needs the feed's latest sign-off to be meets or meets_with_gaps on its latest profile.
  It copies the licence, permitted use and redistribution from the catalogue entry at that moment, names
  the owner (a user who holds data_owner) and the visibility (open or restricted), writes the
  feed_registrations row, then the feed becomes registered. The owner is told. The requirements stay
  onboarding. no_owner: none named and none on the feed, or the named user is not a data owner; nothing
  is written. Phase 5: a registration also queues the feed's glossary run (step 05) in the same
  transaction; the API starts the worker after the commit.
- link_to_registered_feed(): a requirement handed to a source whose feed is already registered (one feed
  per source). By rule, at once: it moves to onboarding; nothing is fetched, profiled, signed off or
  registered again. The backend team is told. People decided when the feed was registered.

Lock: the feed row first (FOR NO KEY UPDATE), then the stage, the sign-off and the next version are read.
Rules execute: no model call. The step flushes; the caller commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy.orm import Session

from core.domain.feed import Feed, FeedRegistration, FeedVisibility
from core.repositories.errors import (
    FeedHasProduct,
    FeedNotACandidate,
    FeedNotRegistered,
    FeedNotSignedOff,
    NotAnApprover,
    RequirementNotInStatus,
)
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feed_signoffs import FeedSignoffRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary_runs import GlossaryRunRepository
from core.repositories.products import ProductRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.steps.result import StepResult
from core.steps.s05_glossary import queue_run
from ports.connectors.catalogue import SourceCatalogue
from ports.identity.base import Identity, User
from ports.notify.base import Notifier


class Registered(StepResult):
    outcome: Literal["registered"] = "registered"
    feed: Feed
    registration: FeedRegistration
    glossary_run_id: UUID | None  # phase 5: the glossary run queued in the same transaction (None: not queued, 7.22)


class DuplicateLinked(StepResult):
    outcome: Literal["duplicate_linked"] = "duplicate_linked"
    feed_code: str
    requirement_id: UUID
    owner_id: str
    registered_at: datetime


class NoOwner(StepResult):
    outcome: Literal["no_owner"] = "no_owner"
    reason: Literal["none_named", "not_a_data_owner"]


RegisterResult = Annotated[Registered | DuplicateLinked | NoOwner, Field(discriminator="outcome")]


def register_feed(session: Session, *, feed_id: UUID, user: User, owner_id: str | None, visibility: FeedVisibility,
                  catalogue: SourceCatalogue, identity: Identity, notifier: Notifier,
                  queue_glossary: bool = True) -> Registered | NoOwner:
    """`queue_glossary=False` (the onboarding, phase 7.22): the feed is mapped to the existing glossary first, and a
    glossary element is asked for only per column, by a person; no run is queued here."""
    feeds = FeedRepository(session)
    feed = feeds.lock(feed_id)  # before the stage, the sign-off and the next version are read
    if feed.stage != "candidate":
        raise FeedNotACandidate(f"Feed {feed.code!r} is {feed.stage}; only a candidate feed is registered")
    signoff = FeedSignoffRepository(session).latest(feed.id)
    profile = FeedProfileRepository(session).latest(feed.id)
    if signoff is None or signoff.outcome == "rejected" or profile is None or signoff.profile_id != profile.id:
        raise FeedNotSignedOff(f"Feed {feed.code!r} needs its latest profile signed off as meets or "
                               "meets_with_gaps before it is registered")
    owner = (owner_id or "").strip() or feed.owner_id
    if not owner:
        return NoOwner(reason="none_named")
    person = identity.get_user(owner)
    if person is None or "data_owner" not in person.roles:
        return NoOwner(reason="not_a_data_owner")
    conditions = catalogue.get(feed.source_code or "").conditions  # UnknownSource if it left the catalogue
    registration = FeedRegistrationRepository(session).add_version(
        feed_id=feed.id, signoff_id=signoff.id, owner_id=owner, licence=conditions.licence,
        permitted_use=conditions.permitted_use, redistribution=conditions.redistribution, visibility=visibility,
        registered_by=user.id)
    registered = feeds.register(feed.id, owner_id=owner)
    run = queue_run(session, feed_id=feed.id, reason="registered", requested_by=user.id)[0] if queue_glossary else None
    notifier.notify(recipient=owner, subject=f"Feed registered: {feed.code}",
                    body=f"Feed {feed.code} ({feed.name}) is registered, {visibility}, under {conditions.licence}. "
                         "You are its owner.")
    return Registered(feed=registered, registration=registration, glossary_run_id=run.id if run else None)


def revise_visibility(session: Session, *, feed_id: UUID, user: User, visibility: FeedVisibility) -> Registered:
    """Phase 7.8, task 7.3a: the owner chooses the visibility at go-live, after the feed is registered. A different
    visibility is a new registration version (migration 0026: same owner, licence, permitted use, redistribution and
    sign-off, before the feed has a product); the same visibility writes nothing and returns the latest. The feed row
    is locked first, and the latest version is read after the lock. Only the latest registration's owner may revise:
    any other user (a backend user, another data owner) gets NotAnApprover, also when the visibility is the same, and
    nothing is written. FeedNotRegistered, FeedHasProduct, NotAnApprover. No new glossary run: nothing about the
    columns changed, so `glossary_run_id` is the feed's latest run."""
    feed = FeedRepository(session).lock(feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}, not registered")
    registrations = FeedRegistrationRepository(session)
    latest = registrations.current(feed.id)
    if latest is None:
        raise FeedNotRegistered(f"Feed {feed.code!r} has no registration")
    if user.id != latest.owner_id:
        raise NotAnApprover(f"Only the owner of feed {feed.code!r} ({latest.owner_id}) chooses its visibility")
    if latest.visibility != visibility:
        if ProductRepository(session).for_feed(feed.id) is not None:
            raise FeedHasProduct(f"Feed {feed.code!r} has a product: its visibility ({latest.visibility}) is fixed")
        latest = registrations.add_version(
            feed_id=feed.id, signoff_id=latest.signoff_id, owner_id=latest.owner_id, licence=latest.licence,
            permitted_use=latest.permitted_use, redistribution=latest.redistribution, visibility=visibility,
            registered_by=user.id)
    run = GlossaryRunRepository(session).latest(feed.id)
    return Registered(feed=feed, registration=latest, glossary_run_id=run.id if run else None)


def link_to_registered_feed(session: Session, *, requirement_id: UUID, feed_id: UUID, notifier: Notifier,
                            backend_team: Sequence[str]) -> DuplicateLinked:
    """Run right after a selection of the feed is committed, in place of the first fetch."""
    feed = FeedRepository(session).get(feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}, not registered")
    requirements = RequirementRepository(session)
    requirement = requirements.lock(requirement_id)
    current = SourceChoiceRepository(session).current(requirement_id)
    if requirement.status != "source_selected" or current is None or current.action != "selected" \
            or current.feed_id != feed.id:
        raise RequirementNotInStatus(f"Requirement {requirement_id} is {requirement.status}; only a requirement that "
                                     f"has just selected feed {feed.code!r} is linked to it")
    requirements.advance(requirement_id, "onboarding")
    for user_id in backend_team:
        notifier.notify(recipient=user_id, subject=f"Linked to a registered feed: {feed.code}",
                        body=f"Requirement {requirement_id} ({requirement.title}) selected source {feed.source_code}, "
                             f"whose feed {feed.code} is registered. It is onboarding; nothing was fetched again.")
    return DuplicateLinked(feed_code=feed.code, requirement_id=requirement_id, owner_id=feed.owner_id,
                           registered_at=feed.registered_at)
