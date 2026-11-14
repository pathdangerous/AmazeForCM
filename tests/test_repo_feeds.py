"""FeedRepository: the feed registry. A feed starts as a candidate; registering it needs an owner
(the no_owner outcome of step 04), which the database also enforces."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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


def test_list_with_source_skips_feeds_without_a_connector_and_rejected_feeds(repo, db_session):
    with_source = repo.create_candidate(code="feed_a", name="Feed A", **SOURCE)
    repo.create_candidate(code="feed_b", name="Feed B")
    rejected = repo.create_candidate(code="feed_c", name="Feed C", **{**SOURCE, "source_code": "syn_other"})
    db_session.execute(text("UPDATE feeds SET stage = 'rejected' WHERE id = :id"), {"id": rejected.id})
    db_session.expire_all()
    assert [f.id for f in repo.list_with_source()] == [with_source.id]


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


def test_register_with_owner(repo):
    feed = repo.create_candidate(code="feed_a", name="Feed A")
    registered = repo.register(feed.id, owner_id="dev-data-owner")
    assert (registered.stage, registered.owner_id) == ("registered", "dev-data-owner")
    assert registered.registered_at is not None


def test_register_uses_the_owner_already_set(repo):
    feed = repo.create_candidate(code="feed_a", name="Feed A", owner_id="dev-data-owner")
    assert repo.register(feed.id).owner_id == "dev-data-owner"


def test_register_without_owner_is_no_owner(repo):
    feed = repo.create_candidate(code="feed_a", name="Feed A")
    with pytest.raises(FeedHasNoOwner):
        repo.register(feed.id)
    assert repo.get(feed.id).stage == "candidate"


def test_register_twice(repo):
    feed = repo.create_candidate(code="feed_a", name="Feed A")
    repo.register(feed.id, owner_id="dev-data-owner")
    with pytest.raises(FeedNotACandidate):
        repo.register(feed.id, owner_id="dev-data-owner")


def test_list_by_stage_newest_first(repo):
    a = repo.create_candidate(code="feed_a", name="Feed A")
    b = repo.create_candidate(code="feed_b", name="Feed B")
    repo.register(a.id, owner_id="dev-data-owner")
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
    with pytest.raises(IntegrityError, match="fk_proposals_feed_id_feeds"):
        make_proposal(feed_id=uuid.uuid4())
