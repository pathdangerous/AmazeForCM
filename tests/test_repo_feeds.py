"""FeedRepository: the feed registry. A feed starts as a candidate; registering it needs an owner
(the no_owner outcome of step 04), which the database also enforces."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from core.repositories.errors import (
    DuplicateFeedCode,
    DuplicateFeedSource,
    FeedHasNoOwner,
    FeedNotACandidate,
    FeedNotFound,
    RequirementNotFound,
)
from core.repositories.feeds import FeedRepository
from core.repositories.requirements import RequirementRepository
from tests.onboarded import registered_feed, registration, rejected_feed, signed_off


@pytest.fixture
def repo(db_session):
    return FeedRepository(db_session)


@pytest.fixture
def requirement(db_session):
    return RequirementRepository(db_session).create(requester_id="dev-requester", title="Synthetic prices")


def test_registry_starts_empty(repo):
    assert repo.list() == []


def test_create_candidate(repo, requirement):
    feed = repo.create_candidate(code="synthetic_bond_prices", name="Synthetic bond prices",
                                 requirement_id=requirement.id, source_ref="vendor_a/prices.csv")
    assert (feed.stage, feed.owner_id, feed.registered_at) == ("candidate", None, None)
    assert repo.get(feed.id) == feed
    assert repo.get_by_code("synthetic_bond_prices") == feed


def test_duplicate_code(repo):
    repo.create_candidate(code="feed_a", name="Feed A")
    with pytest.raises(DuplicateFeedCode, match="feed_a"):
        repo.create_candidate(code="feed_a", name="Another")


def test_unknown_requirement(repo):
    with pytest.raises(RequirementNotFound):
        repo.create_candidate(code="feed_a", name="Feed A", requirement_id=uuid.uuid4())


# ---- phase 3: a candidate feed can carry its source (one feed per source)

SOURCE = {"source_code": "syn_exchange_volumes", "connector": "local_file",
          "source_ref": "files/exchange_volumes.csv"}


def test_candidate_with_a_source(repo):
    feed = repo.create_candidate(code="syn_exchange_volumes", name="Exchange volumes", **SOURCE)
    assert (feed.source_code, feed.connector, feed.source_ref) == (
        "syn_exchange_volumes", "local_file", "files/exchange_volumes.csv",
    )
    assert repo.get_by_source("syn_exchange_volumes") == feed
    assert repo.get_by_source("syn_nothing") is None


def test_one_feed_per_source(repo):
    repo.create_candidate(code="feed_a", name="Feed A", **SOURCE)
    with pytest.raises(DuplicateFeedSource, match="syn_exchange_volumes"):
        repo.create_candidate(code="feed_b", name="Feed B", **SOURCE)


def test_list_to_fetch_skips_feeds_without_a_connector_and_rejected_feeds(repo, db_session):
    """Registered feeds, so a requirement's selection plays no part here (test_jobs.py covers it; Q4)."""
    with_source = registered_feed(db_session, repo.create_candidate(code="feed_a", name="Feed A", **SOURCE).id)
    registered_feed(db_session, repo.create_candidate(code="feed_b", name="Feed B").id)
    rejected = repo.create_candidate(code="feed_c", name="Feed C", **{**SOURCE, "source_code": "syn_other"})
    rejected_feed(db_session, rejected.id)  # phase 4: the stage guard refuses a bare UPDATE (0014)
    assert [f.id for f in repo.list_to_fetch()] == [with_source.id]


def test_list_to_fetch_lists_a_registered_dropzone_feed(repo, db_session):
    """Phase 7.8, task 4.4 (was: left a dropzone feed out until the connector existed, task 2.4): a registered
    uploaded feed (connector dropzone) is fetched by the daily job like a registered file feed."""
    file_feed = registered_feed(db_session, repo.create_candidate(code="feed_a", name="Feed A", **SOURCE).id)
    uploaded = repo.create_candidate(code="upload_a", name="Upload A", source_code="upl_upload_a",
                                     connector="dropzone", source_ref="dropzone/upload_a")
    registered_feed(db_session, uploaded.id)
    assert [f.id for f in repo.list_to_fetch()] == [file_feed.id, uploaded.id]
    candidate = repo.create_candidate(code="upload_b", name="Upload B", source_code="upl_upload_b",
                                      connector="dropzone", source_ref="dropzone/upload_b")
    assert candidate.id not in [f.id for f in repo.list_to_fetch()]  # a candidate nobody selects stays idle


def test_list_to_fetch_leaves_out_a_candidate_feed_no_requirement_selects(repo):
    repo.create_candidate(code="feed_a", name="Feed A", **SOURCE)
    assert repo.list_to_fetch() == []


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        ("source_code, connector, source_ref) VALUES ('f', 'f', 's', 'ftp', 'x'",
         "ck_feeds_connector"),
        ("source_code, source_ref) VALUES ('f', 'f', 's', 'x'", "ck_feeds_source_and_connector_together"),
        ("connector, source_ref) VALUES ('f', 'f', 'local_file', 'x'", "ck_feeds_source_and_connector_together"),
        ("source_code, connector) VALUES ('f', 'f', 's', 'local_file'", "ck_feeds_connector_has_source_ref"),
    ],
)
def test_database_refuses_an_incomplete_source(db_session, columns, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        db_session.execute(text(f"INSERT INTO feeds (code, name, {columns})"))


def test_database_refuses_a_second_feed_for_one_source(db_session):
    db_session.execute(text("INSERT INTO feeds (code, name, source_code, connector, source_ref) "
                            "VALUES ('a', 'A', 's', 'local_file', 'x')"))
    with pytest.raises(IntegrityError, match="uq_feeds_source_code"):
        db_session.execute(text("INSERT INTO feeds (code, name, source_code, connector, source_ref) "
                                "VALUES ('b', 'B', 's', 'local_file', 'x')"))


def test_register_with_owner(repo, db_session):
    feed = repo.create_candidate(code="feed_a", name="Feed A")
    registration(db_session, feed.id)  # phase 4: the stage guard needs the registration row first (0014)
    registered = repo.register(feed.id, owner_id="dev-data-owner")
    assert (registered.stage, registered.owner_id) == ("registered", "dev-data-owner")
    assert registered.registered_at is not None


def test_register_uses_the_owner_already_set(repo, db_session):
    feed = repo.create_candidate(code="feed_a", name="Feed A", owner_id="dev-data-owner")
    registration(db_session, feed.id)
    assert repo.register(feed.id).owner_id == "dev-data-owner"


def test_register_without_owner_is_no_owner(repo):
    feed = repo.create_candidate(code="feed_a", name="Feed A")
    with pytest.raises(FeedHasNoOwner):
        repo.register(feed.id)
    assert repo.get(feed.id).stage == "candidate"


def test_register_twice(repo, db_session):
    feed = registered_feed(db_session, repo.create_candidate(code="feed_a", name="Feed A").id)
    with pytest.raises(FeedNotACandidate):
        repo.register(feed.id, owner_id="dev-data-owner")


def test_reject_a_candidate_with_a_rejected_signoff(repo, db_session):
    feed = repo.create_candidate(code="feed_a", name="Feed A")
    signed_off(db_session, feed.id, outcome="rejected", reasons=["No data to check"])
    assert repo.reject(feed.id).stage == "rejected"
    with pytest.raises(FeedNotACandidate):
        repo.reject(feed.id)
    with pytest.raises(FeedNotFound):
        repo.reject(uuid.uuid4())


def test_the_database_refuses_a_rejection_without_a_rejected_signoff(repo):
    feed = repo.create_candidate(code="feed_a", name="Feed A")
    with pytest.raises(DBAPIError, match="needs a rejected sign-off"):
        repo.reject(feed.id)


def test_list_by_stage_newest_first(repo, db_session):
    a = repo.create_candidate(code="feed_a", name="Feed A")
    b = repo.create_candidate(code="feed_b", name="Feed B")
    registered_feed(db_session, a.id)
    assert [f.code for f in repo.list()] == ["feed_b", "feed_a"]
    assert [f.code for f in repo.list(stage="registered")] == ["feed_a"]
    assert [f.code for f in repo.list(stage="candidate")] == ["feed_b"]


def test_unknown_feed(repo):
    with pytest.raises(FeedNotFound):
        repo.get(uuid.uuid4())
    with pytest.raises(FeedNotFound):
        repo.get_by_code("nope")
    with pytest.raises(FeedNotFound):
        repo.register(uuid.uuid4(), owner_id="dev-data-owner")


@pytest.mark.parametrize("bad", [{"code": "Feed A"}, {"name": " "}])
def test_create_validation(repo, bad):
    with pytest.raises(ValidationError):
        repo.create_candidate(**{"code": "feed_a", "name": "Feed A", **bad})


def test_database_refuses_a_registered_feed_without_owner(db_session):
    with pytest.raises(IntegrityError, match="ck_feeds_registered_has_owner"):
        db_session.execute(text(
            "INSERT INTO feeds (code, name, stage, registered_at) VALUES ('feed_x', 'X', 'registered', now())"
        ))


def test_proposal_links_must_point_at_real_rows(db_session, make_proposal):
    # old: the FK fk_proposals_feed_id_feeds. 0027 (phase 7.9) replaced it with an insert trigger, so a proposal kept
    # after its feed was deleted may name a feed that is gone, while a new proposal still needs a real one.
    with pytest.raises(IntegrityError, match="proposals.feed_id: feed .* does not exist"):
        make_proposal(feed_id=uuid.uuid4())
