"""LoadRepository: one delivery of a registered feed (steps 12-15). Phase 7 adds statuses such as
quarantined by migration."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.repositories.errors import FeedNotFound, FeedNotRegistered, LoadNotFound
from core.repositories.feeds import FeedRepository
from core.repositories.loads import LoadRepository


@pytest.fixture
def feeds(db_session):
    return FeedRepository(db_session)


@pytest.fixture
def registered(feeds):
    feed = feeds.create_candidate(code="synthetic_prices", name="Synthetic prices")
    return feeds.register(feed.id, owner_id="dev-data-owner")


@pytest.fixture
def repo(db_session):
    return LoadRepository(db_session)


def test_create_on_a_registered_feed(repo, registered):
    load = repo.create(feed_id=registered.id, file_ref="local://raw/synthetic_prices/2026-01-02.csv")
    assert load.status == "received"
    assert load.received_at is not None
    assert repo.get(load.id) == load


def test_only_registered_feeds_receive_loads(repo, feeds):
    candidate = feeds.create_candidate(code="not_yet", name="Not yet")
    with pytest.raises(FeedNotRegistered):
        repo.create(feed_id=candidate.id, file_ref="local://raw/x.csv")
    with pytest.raises(FeedNotFound):
        repo.create(feed_id=uuid.uuid4(), file_ref="local://raw/x.csv")


def test_list_for_feed_newest_first(repo, registered):
    ids = [repo.create(feed_id=registered.id, file_ref=f"local://raw/{n}.csv").id for n in range(3)]
    assert [load.id for load in repo.list_for_feed(registered.id)] == list(reversed(ids))


@pytest.mark.parametrize("status", ["held", "failed_rolled_back", "released"])
def test_set_status(repo, registered, status):
    load = repo.create(feed_id=registered.id, file_ref="local://raw/x.csv")
    assert repo.set_status(load.id, status).status == status


def test_invalid_status_and_unknown_load(repo, registered):
    load = repo.create(feed_id=registered.id, file_ref="local://raw/x.csv")
    with pytest.raises(ValidationError):
        repo.set_status(load.id, "quarantined")  # added by migration in phase 7, not yet
    with pytest.raises(LoadNotFound):
        repo.get(uuid.uuid4())


def test_database_refuses_unknown_status(db_session, registered):
    with pytest.raises(IntegrityError, match="ck_loads_status"):
        db_session.execute(
            text("INSERT INTO loads (feed_id, file_ref, status) VALUES (:f, 'local://raw/x.csv', 'lost')"),
            {"f": registered.id},
        )
