"""FeedSignoffRepository: step 03's sign-offs, versioned per feed (phase 4 plan, assumption 19)."""

import pytest
from pydantic import ValidationError

from core.repositories.feed_signoffs import FeedSignoffRepository
from core.repositories.feeds import FeedRepository
from tests.onboarded import BACKEND, profiled, signed_off, summary


@pytest.fixture
def feed(db_session):
    return FeedRepository(db_session).create_candidate(code="syn_a", name="A", source_code="syn_a",
                                                       connector="local_file", source_ref="files/a.csv")


def test_sign_offs_are_versioned_and_keep_gaps_and_reasons(db_session, feed):
    repo = FeedSignoffRepository(db_session)
    assert repo.latest(feed.id) is None
    first = signed_off(db_session, feed.id, outcome="meets_with_gaps", gaps=["issue_date empty in 2 of 10 rows"])
    second = signed_off(db_session, feed.id, outcome="rejected", reasons=["No data to check"])
    assert (first.version, second.version) == (1, 2)
    assert (first.gaps, first.reasons, second.gaps, second.reasons) == (
        ["issue_date empty in 2 of 10 rows"], [], [], ["No data to check"])
    assert repo.latest(feed.id) == second
    assert repo.history(feed.id) == [first, second]


def test_the_draft_is_checked_before_the_database(db_session, feed):
    profile = profiled(db_session, feed.id)
    proposal = summary(db_session, profile)
    with pytest.raises(ValidationError):
        FeedSignoffRepository(db_session).add_version(feed_id=feed.id, profile_id=profile.id, proposal_id=proposal.id,
                                                      outcome="rejected", signed_off_by=BACKEND)
