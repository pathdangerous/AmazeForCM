"""Phase 7.16 T2: POST /api/amaze/feeds/{id}/profiles/erase and the audit endpoint's erasure rows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from ports.config import Settings
from tests.conftest import running_app
from tests.onboarded import profiled, registered_feed
from tests.test_commit_visibility import committed, own_schema  # noqa: F401 (own_schema is a fixture)
from tests.test_profile_erase import WHY, feed  # noqa: F401 (feed is a fixture)

pytestmark = pytest.mark.anyio
OWNER = {"X-Dev-User": "dev-data-owner"}
OPERATOR = {"X-Dev-User": "dev-operator"}
STEWARD = {"X-Dev-User": "dev-data-steward"}
MONTH = f"{datetime.now(UTC):%Y-%m}"


@pytest.fixture
async def client(db_session, migrated_engine):
    async with running_app(engine=migrated_engine, session=db_session, settings=Settings(_env_file=None)) as c:
        yield c


@pytest.fixture
def saved(db_session, feed):
    """The feed committed to the test's savepoint, so a refused request's rollback keeps it."""
    db_session.commit()
    return feed


def url(feed) -> str:
    return f"/api/amaze/feeds/{feed.id}/profiles/erase"


async def test_the_owner_erases_and_the_returned_page_shows_it(client, saved):
    response = await client.post(url(saved), headers=OWNER, json={"before_version": 4, "reason": WHY})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["outcome"] == "profiles_erased" and body["result"]["erased_versions"] == [2, 3]
    assert [p["version"] for p in body["feed"]["profiles"]] == [1, 4]
    assert [(e["by"], e["erased_versions"]) for e in body["feed"]["erasures"]] == [("dev-data-owner", [2, 3])]


async def test_nothing_to_erase_is_an_answer_not_an_error(client, saved):
    response = await client.post(url(saved), headers=OWNER, json={"before_version": 2, "reason": WHY})
    assert response.status_code == 200
    assert response.json()["result"]["outcome"] == "nothing_to_erase"
    assert response.json()["result"]["kept"][0]["reason"] == "registration"


@pytest.mark.parametrize("headers, body, status", [
    (STEWARD, {"before_version": 4, "reason": WHY}, 403),  # neither the owner nor operations
    ({"X-Dev-User": "dev-requester"}, {"before_version": 4, "reason": WHY}, 403),  # not a backend person
    (OWNER, {"before_version": 4, "reason": "  "}, 422), (OWNER, {"before_version": 99, "reason": WHY}, 422),
    (OWNER, {"before_version": 4}, 422), (OWNER, {"before_version": 4, "reason": WHY, "force": True}, 422)])
async def test_refusals_have_their_status_and_erase_nothing(client, saved, headers, body, status):
    assert (await client.post(url(saved), headers=headers, json=body)).status_code == status
    page = (await client.get(f"/api/amaze/feeds/{saved.id}", headers=OPERATOR)).json()
    assert [p["version"] for p in page["profiles"]] == [1, 2, 3, 4]


async def test_an_unknown_feed_is_404(client):
    assert (await client.post(f"/api/amaze/feeds/{uuid.uuid4()}/profiles/erase", headers=OPERATOR,
                              json={"before_version": 2, "reason": WHY})).status_code == 404


async def test_the_audit_endpoint_and_its_csv_list_the_erase(client, saved):
    await client.post(url(saved), headers=OWNER, json={"before_version": 4, "reason": WHY})
    rows = (await client.get(f"/api/amaze/audit?month={MONTH}&feed={saved.id}", headers=OPERATOR)).json()["rows"]
    [erased] = [r for r in rows if r["kind"] == "erasure"]
    assert (erased["who"], erased["decision"], erased["item"], erased["note"]) == ("dev-data-owner", "erased", "profiles", WHY)
    csv_text = (await client.get(f"/api/amaze/audit.csv?month={MONTH}", headers=OPERATOR)).text
    assert "erasure" in csv_text and "erased" in csv_text


async def test_erasing_commits_the_deletion_and_its_record(own_schema):
    from core.repositories.feeds import FeedRepository
    from ports.db import get_sessionmaker

    with get_sessionmaker(own_schema)() as session:
        row = FeedRepository(session).create_candidate(code="syn_erase_commit", name="Erase", source_code="syn_erase_commit",
                                                       connector="local_file", source_ref="files/x.csv")
        registered_feed(session, row.id)
        profiled(session, row.id)
        profiled(session, row.id)
        session.commit()
        feed_id = row.id
    async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
        response = await client.post(f"/api/amaze/feeds/{feed_id}/profiles/erase", headers=OWNER,
                                     json={"before_version": 3, "reason": WHY})
        assert response.status_code == 200, response.text
    assert committed(own_schema, "SELECT version FROM feed_profiles WHERE feed_id = :f ORDER BY version", f=feed_id) == [(1,), (3,)]
    assert committed(own_schema, "SELECT \"by\", reason, erased_versions FROM profile_erasures") == [("dev-data-owner", WHY, [2])]
