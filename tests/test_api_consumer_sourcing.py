"""Consumer API for the custom data product, part 3a: the requirement page and "Find a source".

GET  /api/consumer/requirements/{id}               status, the requester's message, source, last fetch
POST /api/consumer/requirements/{id}/find-source   Known source?; for a known source the selection,
                                                   then the first fetch in its own transaction

The requester only, and only for their own requirement (another's is 404). A requirement that is not
defined gives 409. Phase 3 plan, assumptions 2, 9, 13, 14 and 43.
"""

import pytest

from apps.api.deps import get_blob_store_port
from core.repositories.consumer_requests import ConsumerRequestRepository
from core.repositories.requirements import RequirementRepository
from core.seed import seed_demo, seed_glossary
from ports.config import Settings
from ports.storage.local import LocalBlobStore
from tests.conftest import running_app

pytestmark = pytest.mark.anyio

RILEY = {"X-Dev-User": "dev-requester"}
BLAKE = {"X-Dev-User": "dev-backend-team"}
ONBOARDING = "Your data is getting onboarded; we will notify you once it is ready."
RAISED = "Requirement raised; the backend team will look for a source."


@pytest.fixture
async def client(db_session, migrated_engine, tmp_path):
    """The real catalogue and sample files; a temporary blob store, so no file lands in data/blob."""
    from apps.api.main import app

    seed_glossary(db_session)
    seed_demo(db_session)
    db_session.commit()
    blobs = LocalBlobStore(tmp_path / "blob")
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None), session=db_session) as c:
        app.dependency_overrides[get_blob_store_port] = lambda: blobs
        c.blobs = blobs
        yield c


async def raise_requirement(client, query, element_keys=(), terms=()):
    response = await client.post("/api/consumer/requests", headers=RILEY, json={
        "query": query, "element_keys": list(element_keys), "terms": list(terms),
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["routing"]["outcome"] == "raise_requirement"
    return body["requirement"]["requirement"]["id"]


async def find_source(client, requirement_id, headers=RILEY, expect=200):
    response = await client.post(f"/api/consumer/requirements/{requirement_id}/find-source", headers=headers)
    assert response.status_code == expect, response.text
    return response.json()


async def page(client, requirement_id, headers=RILEY, expect=200):
    response = await client.get(f"/api/consumer/requirements/{requirement_id}", headers=headers)
    assert response.status_code == expect, response.text
    return response.json()


async def test_done_when_a_known_source_is_fetched_and_the_requester_sees_onboarding(client):
    """3a done-when, item 2: row A of the plan."""
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    before = await page(client, requirement_id)
    assert (before["status"], before["message"], before["next_action"]) == ("defined", RAISED, "find_source")

    found = await find_source(client, requirement_id)
    assert (found["check"]["outcome"], found["check"]["source"]["code"]) == ("known_source", "syn_exchange_volumes")
    assert found["fetch"]["outcome"] == "fetched"
    assert found["message"] == ONBOARDING

    after = await page(client, requirement_id)
    assert (after["status"], after["message"], after["next_action"]) == ("onboarding", ONBOARDING, None)
    assert (after["source"]["code"], after["source"]["feed_code"]) == ("syn_exchange_volumes", "syn_exchange_volumes")
    assert after["source"]["conditions"]["licence"] == "Synthetic exchange data licence"
    assert after["last_fetch"]["outcome"] == "fetched"
    assert client.blobs.get(found["fetch"]["blob_ref"]).startswith(b"instr_id,trade_date,traded_qty")

    feeds = (await client.get("/api/amaze/feeds", headers=BLAKE)).json()
    assert [(f["code"], f["stage"], f["requirement_id"]) for f in feeds] == [
        ("syn_exchange_volumes", "candidate", requirement_id),
    ]


async def test_a_known_source_that_does_not_answer_waits(client):
    """Row B: the selection stands; the requirement waits as source_selected."""
    requirement_id = await raise_requirement(client, "accrued interest", ["accrued_interest"])
    found = await find_source(client, requirement_id)
    assert (found["check"]["outcome"], found["fetch"]["outcome"]) == ("known_source", "source_down")
    message = "A source was found: Accrued interest feed. It did not answer; Amaze tries again every day."
    assert found["message"] == message
    after = await page(client, requirement_id)
    assert (after["status"], after["message"], after["last_fetch"]["outcome"]) == ("source_selected", message,
                                                                                  "source_down")


@pytest.mark.parametrize(
    ("query", "element_keys", "terms", "reason"),
    [
        ("open price", ["open_price"], [], "no_known_source_covers"),  # row C
        ("esg score", [], ["esg score"], "terms_ticked"),  # row D
    ],
)
async def test_no_known_source_changes_nothing(client, query, element_keys, terms, reason):
    requirement_id = await raise_requirement(client, query, element_keys, terms)
    found = await find_source(client, requirement_id)
    assert (found["check"]["outcome"], found["check"]["reason"], found["fetch"]) == ("no_known_source", reason, None)
    assert found["message"] == "No known source has this data yet."
    after = await page(client, requirement_id)
    assert (after["status"], after["source"], after["last_fetch"], after["next_action"]) == (
        "defined", None, None, "find_source",
    )


async def test_find_source_needs_status_defined(client):
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, requirement_id)
    second = await find_source(client, requirement_id, expect=409)
    assert "onboarding" in second["detail"]


async def test_another_requesters_requirement_is_404(client, db_session):
    other = RequirementRepository(db_session).create(requester_id="dev-someone-else", title="volume",
                                                     element_keys=["volume"])
    db_session.commit()
    await page(client, other.id, expect=404)
    await find_source(client, other.id, expect=404)
    assert RequirementRepository(db_session).get(other.id).status == "defined"
    await page(client, "00000000-0000-4000-8000-000000000000", expect=404)


async def test_backend_users_and_no_user(client):
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await page(client, requirement_id, headers=BLAKE, expect=403)
    await find_source(client, requirement_id, headers=BLAKE, expect=403)
    await page(client, requirement_id, headers={}, expect=401)
    await find_source(client, requirement_id, headers={}, expect=401)


async def test_my_requests_links_each_raised_requirement(client, db_session):
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, requirement_id)
    mine = (await client.get("/api/consumer/requests", headers=RILEY)).json()
    assert [(r["requirement_id"], r["requirement_status"]) for r in mine] == [(requirement_id, "onboarding")]
    assert ConsumerRequestRepository(db_session).list_for("dev-requester")[0].result["message"] == RAISED
