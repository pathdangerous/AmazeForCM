"""FeedProfileRepository: step 02's profiles, versioned per feed (phase 4 plan, assumption 12)."""

import uuid

import pytest
from pydantic import ValidationError

from core.domain.profiling import FeedProfile
from core.repositories.errors import FeedNotFound
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from tests.onboarded import BACKEND, COLUMN, SPEC, fetched, profiled


@pytest.fixture
def feed(db_session):
    return FeedRepository(db_session).create_candidate(code="syn_a", name="A", source_code="syn_a",
                                                       connector="local_file", source_ref="files/a.csv")


def test_each_profile_is_the_next_version_and_the_highest_is_latest(db_session, feed):
    repo = FeedProfileRepository(db_session)
    assert repo.latest(feed.id) is None
    first, second = profiled(db_session, feed.id), profiled(db_session, feed.id, outcome="spec_only_low_confidence")
    assert (first.version, second.version) == (1, 2)
    assert repo.latest(feed.id) == second
    assert repo.history(feed.id) == [first, second]
    assert repo.get(first.id) == first


def test_columns_coverage_and_spec_read_back_as_models(db_session, feed):
    profile = profiled(db_session, feed.id)
    read = FeedProfileRepository(db_session).get(profile.id)
    assert isinstance(read, FeedProfile)
    assert (read.columns, read.spec, read.row_count, read.engine) == ([COLUMN], SPEC, 1, "duckdb 1.5.6")


@pytest.mark.parametrize("bad", [
    {"outcome": "profiled", "row_count": 0},
    {"outcome": "profiled", "row_count": 1, "detail": "why"},
    {"outcome": "spec_only_low_confidence", "row_count": 0},
    {"outcome": "spec_only_low_confidence", "row_count": 3, "detail": "why"},
])
def test_the_draft_fits_the_outcome(db_session, feed, bad):
    with pytest.raises(ValidationError):
        FeedProfileRepository(db_session).add_version(
            feed_id=feed.id, fetch_id=fetched(db_session, feed.id).id, columns=[COLUMN], coverage=[], spec=SPEC,
            engine="duckdb 1.5.6", profiled_by=BACKEND, **bad)


def test_an_unknown_feed_is_refused_by_name(db_session):
    with pytest.raises(FeedNotFound):
        FeedProfileRepository(db_session).add_version(
            feed_id=uuid.uuid4(), fetch_id=uuid.uuid4(), outcome="profiled", row_count=1, columns=[COLUMN],
            coverage=[], spec=SPEC, engine="duckdb 1.5.6", profiled_by=BACKEND)
