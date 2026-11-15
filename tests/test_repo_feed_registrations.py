"""FeedRegistrationRepository: step 04's registrations, versioned per feed (phase 4 plan, assumption 29)."""

import pytest
from pydantic import ValidationError

from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feeds import FeedRepository
from tests.onboarded import BACKEND, registration, signed_off


@pytest.fixture
def feed(db_session):
    return FeedRepository(db_session).create_candidate(code="syn_a", name="A", source_code="syn_a",
                                                       connector="local_file", source_ref="files/a.csv")


def test_a_registration_is_version_1_and_current(db_session, feed):
    repo = FeedRegistrationRepository(db_session)
    assert repo.current(feed.id) is None
    row = registration(db_session, feed.id, visibility="restricted")
    assert (row.version, row.owner_id, row.visibility, row.licence) == (1, "dev-data-owner", "restricted",
                                                                         "Synthetic licence")
    assert repo.current(feed.id) == row


def test_visibility_is_open_or_restricted(db_session, feed):
    signoff = signed_off(db_session, feed.id)
    with pytest.raises(ValidationError):
        FeedRegistrationRepository(db_session).add_version(
            feed_id=feed.id, signoff_id=signoff.id, owner_id="dev-data-owner", licence="L", permitted_use="U",
            redistribution="R", visibility="everyone", registered_by=BACKEND)
