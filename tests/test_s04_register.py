"""Step 04 register (phase 4 plan, assumptions 28 to 33).

Outcomes (CLAUDE.md): registered; duplicate_linked; no_owner. Register needs the feed's latest sign-off
to accept its latest profile. It copies the licence from the catalogue, names an owner who holds
data_owner and a visibility, and tells the owner. A requirement handed to a source whose feed is already
registered is linked to it at once (duplicate_linked): nothing is fetched, profiled or registered again.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from core.repositories.errors import (
    FeedNotACandidate,
    FeedNotRegistered,
    FeedNotSignedOff,
    RequirementNotInStatus,
)
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feeds import FeedRepository
from core.repositories.notifications import NotificationRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.known_source import find_known_source
from core.steps.s04_register import DuplicateLinked, NoOwner, Registered, link_to_registered_feed, register_feed
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from ports.identity.dev import DevIdentity
from ports.notify.inapp import InAppNotifier
from tests.conftest import dev_user_for
from tests.onboarded import profiled, signed_off

CATALOGUE = SourceCatalogue.load(DEFAULT_CATALOGUE)
BACKEND = dev_user_for("backend_team")
TEAM = ["dev-backend-team"]


@pytest.fixture
def volumes(db_session, glossary):
    """volume on the known source syn_exchange_volumes, onboarding; returns (requirement, feed)."""
    req = RequirementRepository(db_session).create(requester_id="dev-requester", title="volume",
                                                   element_keys=["volume"])
    feed = find_known_source(db_session, requirement_id=req.id, catalogue=CATALOGUE).feed
    RequirementRepository(db_session).advance(req.id, "onboarding")
    return req, feed


def register(db_session, feed, owner_id="dev-data-owner", visibility="open"):
    return register_feed(db_session, feed_id=feed.id, user=BACKEND, owner_id=owner_id, visibility=visibility,
                         catalogue=CATALOGUE, identity=DevIdentity(), notifier=InAppNotifier(db_session))


def test_registering_queues_a_glossary_run_for_the_feed(db_session, volumes):
    """Phase 5 plan, assumptions 3 and 10: in the same transaction, reason registered, by the registrant."""
    from core.repositories.glossary_runs import GlossaryRunRepository

    req, feed = volumes
    signoff = signed_off(db_session, feed.id)
    result = register(db_session, feed)
    run = GlossaryRunRepository(db_session).active(feed.id)
    assert (result.glossary_run_id, run.status, run.reason, run.requested_by, run.profile_id) == (
        run.id, "queued", "registered", "dev-backend-team", signoff.profile_id)


def test_no_owner_queues_no_glossary_run(db_session, volumes):
    from core.repositories.glossary_runs import GlossaryRunRepository

    req, feed = volumes
    signed_off(db_session, feed.id)
    assert isinstance(register(db_session, feed, owner_id="dev-requester"), NoOwner)
    assert GlossaryRunRepository(db_session).active(feed.id) is None


def test_registered_with_owner_licence_and_visibility(db_session, volumes):
    req, feed = volumes
    signoff = signed_off(db_session, feed.id, outcome="meets_with_gaps", gaps=["g"])
    result = register(db_session, feed, visibility="restricted")
    assert isinstance(result, Registered)
    assert (result.feed.stage, result.feed.owner_id, result.feed.registered_at is not None) == (
        "registered", "dev-data-owner", True)
    row = result.registration
    assert (row.version, row.signoff_id, row.owner_id, row.visibility, row.registered_by) == (
        1, signoff.id, "dev-data-owner", "restricted", "dev-backend-team")
    assert FeedRegistrationRepository(db_session).current(feed.id) == row
    assert RequirementRepository(db_session).get(req.id).status == "onboarding"


def test_registration_copies_the_licence_from_the_catalogue(db_session, volumes):
    _, feed = volumes
    signed_off(db_session, feed.id)
    row = register(db_session, feed).registration
    conditions = CATALOGUE.get("syn_exchange_volumes").conditions
    assert (row.licence, row.permitted_use, row.redistribution) == (
        conditions.licence, conditions.permitted_use, conditions.redistribution)


def test_the_owner_is_told(db_session, volumes):
    _, feed = volumes
    signed_off(db_session, feed.id)
    register(db_session, feed)
    [told] = NotificationRepository(db_session).list_for("dev-data-owner")
    assert told.subject == "Feed registered: syn_exchange_volumes"
    assert "You are its owner" in told.body


@pytest.mark.parametrize(("owner_id", "reason"), [(None, "none_named"), ("  ", "none_named"),
                                                  ("dev-checker", "not_a_data_owner"),
                                                  ("dev-nobody", "not_a_data_owner")])
def test_no_owner_when_none_is_named_or_the_named_user_is_not_a_data_owner(db_session, volumes, owner_id, reason):
    _, feed = volumes
    signed_off(db_session, feed.id)
    result = register(db_session, feed, owner_id=owner_id)
    assert isinstance(result, NoOwner) and result.reason == reason
    assert FeedRepository(db_session).get(feed.id).stage == "candidate"
    assert FeedRegistrationRepository(db_session).current(feed.id) is None


def test_an_owner_already_on_the_feed_is_used_when_none_is_named(db_session, volumes):
    _, feed = volumes
    db_session.execute(text("UPDATE feeds SET owner_id = 'dev-data-owner' WHERE id = :id"), {"id": feed.id})
    db_session.expire_all()  # a candidate's owner may be set (the stage guard allows it)
    signed_off(db_session, feed.id)
    assert register(db_session, feed, owner_id=None).registration.owner_id == "dev-data-owner"


def test_register_needs_an_accepting_signoff_on_the_latest_profile(db_session, volumes):
    _, feed = volumes
    with pytest.raises(FeedNotSignedOff):
        register(db_session, feed)
    signed_off(db_session, feed.id)
    profiled(db_session, feed.id)  # a newer profile waits for its sign-off
    with pytest.raises(FeedNotSignedOff):
        register(db_session, feed)


def test_a_registered_feed_is_not_registered_again(db_session, volumes, glossary):
    _, feed = volumes
    signed_off(db_session, feed.id)
    register(db_session, feed)
    with pytest.raises(FeedNotACandidate):
        register(db_session, feed)


def test_duplicate_linked_when_the_selected_source_is_already_a_registered_feed(db_session, volumes):
    first, feed = volumes
    signed_off(db_session, feed.id)
    register(db_session, feed)
    fetches = len(SourceFetchRepository(db_session).list_for_feed(feed.id))
    later = RequirementRepository(db_session).create(requester_id="dev-requester", title="volume again",
                                                     element_keys=["volume"])
    found = find_known_source(db_session, requirement_id=later.id, catalogue=CATALOGUE)
    assert found.feed.id == feed.id and found.feed.stage == "registered"
    result = link_to_registered_feed(db_session, requirement_id=later.id, feed_id=feed.id,
                                     notifier=InAppNotifier(db_session), backend_team=TEAM)
    assert isinstance(result, DuplicateLinked)
    assert (result.feed_code, result.requirement_id, result.owner_id) == ("syn_exchange_volumes", later.id,
                                                                          "dev-data-owner")
    assert RequirementRepository(db_session).get(later.id).status == "onboarding"
    assert len(SourceFetchRepository(db_session).list_for_feed(feed.id)) == fetches  # nothing fetched
    assert FeedRegistrationRepository(db_session).current(feed.id).version == 1  # nothing registered again
    assert NotificationRepository(db_session).list_for("dev-backend-team")[0].subject == \
        "Linked to a registered feed: syn_exchange_volumes"


def test_linking_needs_a_registered_feed_and_a_source_selected_requirement(db_session, volumes):
    first, feed = volumes
    with pytest.raises(FeedNotRegistered):
        link_to_registered_feed(db_session, requirement_id=first.id, feed_id=feed.id,
                                notifier=InAppNotifier(db_session), backend_team=TEAM)
    signed_off(db_session, feed.id)
    register(db_session, feed)
    with pytest.raises(RequirementNotInStatus):  # first is onboarding already
        link_to_registered_feed(db_session, requirement_id=first.id, feed_id=feed.id,
                                notifier=InAppNotifier(db_session), backend_team=TEAM)
