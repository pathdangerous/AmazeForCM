"""SourceFetchRepository: every fetch of a feed's source, fetched or source_down. Append-only.

The id can be chosen by the caller, so the blob key can contain it before the row is written
(phase 3 plan, change 2: raw/<feed>/<date>/<fetch id>/<file>).
"""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from core.repositories.errors import FeedNotFound
from core.repositories.feeds import FeedRepository
from core.repositories.source_fetches import SourceFetchRepository


@pytest.fixture
def repo(db_session):
    return SourceFetchRepository(db_session)


@pytest.fixture
def feed(db_session):
    return FeedRepository(db_session).create_candidate(
        code="syn_exchange_volumes", name="Exchange volumes", source_code="syn_exchange_volumes",
        connector="local_file", source_ref="files/exchange_volumes.csv",
    )


def fetched(feed, n=1, fetch_id=None):
    return {"feed_id": feed.id, "outcome": "fetched", "blob_ref": f"raw/f/2026-11-14/{n}/f.csv", "size_bytes": 10 * n,
            **({"fetch_id": fetch_id} if fetch_id else {})}


def down(feed, detail="not found"):
    return {"feed_id": feed.id, "outcome": "source_down", "detail": detail}


def test_record_with_a_chosen_id(repo, feed):
    fetch_id = uuid.uuid4()
    row = repo.record(**fetched(feed, fetch_id=fetch_id))
    assert (row.id, row.outcome, row.blob_ref, row.size_bytes, row.detail) == (
        fetch_id, "fetched", "raw/f/2026-11-14/1/f.csv", 10, None,
    )


def test_latest_and_latest_fetched(repo, feed):
    assert repo.latest(feed.id) is None and repo.latest_fetched(feed.id) is None
    first = repo.record(**fetched(feed, 1))
    second = repo.record(**fetched(feed, 2))
    failed = repo.record(**down(feed))
    assert repo.latest(feed.id) == failed
    assert repo.latest_fetched(feed.id) == second
    assert [f.id for f in repo.list_for_feed(feed.id)] == [failed.id, second.id, first.id]


def test_unknown_feed_and_invalid_rows(repo, feed):
    with pytest.raises(FeedNotFound):
        repo.record(**{**down(feed), "feed_id": uuid.uuid4()})
    with pytest.raises(ValidationError):
        repo.record(**{**fetched(feed), "size_bytes": 0})
    with pytest.raises(ValidationError):
        repo.record(**{**down(feed), "detail": ""})


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ("'lost', NULL, NULL, 'x'", "ck_source_fetches_outcome"),
        ("'fetched', NULL, 10, NULL", "ck_source_fetches_fetched_has_a_file"),
        ("'fetched', 'raw/x', 0, NULL", "ck_source_fetches_fetched_has_a_file"),
        ("'fetched', 'raw/x', NULL, NULL", "ck_source_fetches_fetched_has_a_file"),  # NULL must not pass
        ("'source_down', NULL, NULL, NULL", "ck_source_fetches_down_has_a_reason"),
        ("'source_down', 'raw/x', 10, 'why'", "ck_source_fetches_down_has_a_reason"),
    ],
)
def test_database_refuses_an_inconsistent_fetch(db_session, feed, values, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        db_session.execute(
            text(f"INSERT INTO source_fetches (feed_id, outcome, blob_ref, size_bytes, detail) VALUES (:f, {values})"),
            {"f": feed.id},
        )


def test_database_refuses_an_update(db_session, repo, feed):
    row = repo.record(**down(feed))
    with pytest.raises(DBAPIError, match="source_fetches is append-only"):
        db_session.execute(text("UPDATE source_fetches SET detail = 'x' WHERE id = :id"), {"id": row.id})
