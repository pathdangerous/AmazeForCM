"""Phase 7.16 follow-up: POST /api/amaze/feeds/{id}/profile-again."""

from __future__ import annotations

import pytest

from apps.api.deps import get_blob_store_port
from apps.api.main import app
from ports.config import Settings
from tests.conftest import running_app
from tests.test_commit_visibility import committed, own_schema  # noqa: F401 (own_schema is a fixture)
from tests.test_profile_again import CSV, deliver, world  # noqa: F401 (world is a fixture)

pytestmark = pytest.mark.anyio
BACKEND = {"X-Dev-User": "dev-backend-team"}


@pytest.fixture
async def client(db_session, migrated_engine, world):
    _, blobs = world
    async with running_app(engine=migrated_engine, session=db_session, settings=Settings(_env_file=None)) as c:
        app.dependency_overrides[get_blob_store_port] = lambda: blobs
        yield c


async def test_profile_again_answers_with_the_new_version_and_the_page(client, db_session, world):
    feed_id, blobs = world
    deliver(db_session, blobs, feed_id)
    db_session.commit()
    response = await client.post(f"/api/amaze/feeds/{feed_id}/profile-again", headers=BACKEND)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["result"]["outcome"], body["result"]["profile"]["version"]) == ("profiled_again", 2)
    assert [p["version"] for p in body["feed"]["profiles"]] == [1, 2]
    assert body["feed"]["profiles"][0]["keep_reason"] == "registration" and body["feed"]["profiles"][1]["keep_reason"] == "latest"


async def test_a_file_with_other_columns_is_an_answer_and_a_non_backend_person_is_403(client, db_session, world):
    feed_id, blobs = world
    deliver(db_session, blobs, feed_id, CSV.replace("traded_qty", "other"))
    db_session.commit()
    response = await client.post(f"/api/amaze/feeds/{feed_id}/profile-again", headers=BACKEND)
    assert response.status_code == 200 and response.json()["result"]["outcome"] == "profile_differs"
    assert response.json()["result"]["added"] == ["other"] and len(response.json()["feed"]["profiles"]) == 1
    assert (await client.post(f"/api/amaze/feeds/{feed_id}/profile-again",
                              headers={"X-Dev-User": "dev-requester"})).status_code == 403


async def test_profile_again_commits_the_profile(own_schema, tmp_path):
    from core.domain.profiling import FileColumn
    from core.repositories.feeds import FeedRepository
    from ports.db import get_sessionmaker
    from ports.storage.local import LocalBlobStore
    from tests.onboarded import registered_feed

    blobs = LocalBlobStore(tmp_path / "blob")
    with get_sessionmaker(own_schema)() as session:
        feed = FeedRepository(session).create_candidate(
            code="syn_exchange_volumes", name="Volumes", source_code="syn_exchange_volumes", connector="local_file",
            source_ref="files/exchange_volumes.csv")
        registered_feed(session, feed.id, columns=[
            FileColumn(name=n, type="VARCHAR", null_count=0, null_rate=0.0, distinct_count=1)
            for n in ("instr_id", "trade_date", "traded_qty")])
        deliver(session, blobs, feed.id)
        session.commit()
        feed_id = feed.id
    async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
        app.dependency_overrides[get_blob_store_port] = lambda: blobs
        assert (await client.post(f"/api/amaze/feeds/{feed_id}/profile-again", headers=BACKEND)).status_code == 200
    assert committed(own_schema, "SELECT version, profiled_by FROM feed_profiles WHERE feed_id = :f ORDER BY version",
                     f=feed_id)[-1] == (2, "dev-backend-team")
