"""Amaze (backend) read API for phase 1: the glossary and the feed registry.

Any of the six backend roles may read; no user gives 401 and the requester gets 403.
"""

import pytest

from core.repositories.feeds import FeedRepository
from core.seed import seed_glossary
from ports.config import Settings
from tests.conftest import running_app

pytestmark = pytest.mark.anyio

BACKEND_USERS = ["dev-backend-team", "dev-data-owner", "dev-data-steward", "dev-checker",
                 "dev-glossary-owner", "dev-operations"]
STEWARD = {"X-Dev-User": "dev-data-steward"}


@pytest.fixture
async def client(db_session, migrated_engine):
    seed_glossary(db_session)
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None), session=db_session) as c:
        yield c


async def test_domains_with_counts(client):
    response = await client.get("/api/amaze/glossary/domains", headers=STEWARD)
    assert response.status_code == 200
    assert response.json() == [
        {"domain": "pricing", "label": "Pricing", "count": 14},
        {"domain": "security_master", "label": "Security master", "count": 19},
    ]


async def test_all_elements_and_domain_filter(client):
    everything = (await client.get("/api/amaze/glossary/elements", headers=STEWARD)).json()
    assert len(everything) == 33
    assert {"key", "name", "domain", "data_type", "definition", "aliases", "version", "created_by"} <= set(everything[0])
    for domain, count in (("security_master", 19), ("pricing", 14)):
        listed = (await client.get("/api/amaze/glossary/elements", params={"domain": domain}, headers=STEWARD)).json()
        assert len(listed) == count
        assert {e["domain"] for e in listed} == {domain}


@pytest.mark.parametrize(("q", "keys"), [("isin", ["isin"]), ("CLOSING", ["close_price"]), ("zzz", [])])
async def test_search(client, q, keys):
    listed = (await client.get("/api/amaze/glossary/elements", params={"q": q}, headers=STEWARD)).json()
    assert [e["key"] for e in listed] == keys


async def test_element_detail_with_history(client):
    response = await client.get("/api/amaze/glossary/elements/cusip", headers=STEWARD)
    assert response.status_code == 200
    body = response.json()
    assert body["element"]["key"] == "cusip"
    assert [(v["version"], v["created_by"]) for v in body["history"]] == [(1, "seed")]


async def test_unknown_element_is_404(client):
    response = await client.get("/api/amaze/glossary/elements/no_such_key", headers=STEWARD)
    assert response.status_code == 404
    assert "no_such_key" in response.json()["detail"]


async def test_feed_registry_starts_empty(client):
    response = await client.get("/api/amaze/feeds", headers=STEWARD)
    assert response.status_code == 200
    assert response.json() == []


async def test_feed_registry_lists_and_filters(client, db_session):
    feeds = FeedRepository(db_session)
    feeds.create_candidate(code="synthetic_prices", name="Synthetic prices", source_ref="vendor_a/prices.csv")
    listed = (await client.get("/api/amaze/feeds", headers=STEWARD)).json()
    assert [(f["code"], f["stage"]) for f in listed] == [("synthetic_prices", "candidate")]
    assert (await client.get("/api/amaze/feeds", params={"stage": "registered"}, headers=STEWARD)).json() == []
    assert (await client.get("/api/amaze/feeds", params={"stage": "lost"}, headers=STEWARD)).status_code == 422


@pytest.mark.parametrize("path", ["/api/amaze/glossary/domains", "/api/amaze/glossary/elements", "/api/amaze/feeds"])
async def test_signed_out_is_401_and_requester_is_403(client, path):
    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, headers={"X-Dev-User": "dev-requester"})).status_code == 403


@pytest.mark.parametrize("user", BACKEND_USERS)
async def test_every_backend_role_may_read(client, user):
    assert (await client.get("/api/amaze/glossary/elements", headers={"X-Dev-User": user})).status_code == 200


async def test_database_not_configured_is_503(migrated_engine):
    async with running_app(engine=None, settings=Settings(_env_file=None)) as client:
        from apps.api.deps import get_db_engine
        from apps.api.main import app

        app.dependency_overrides[get_db_engine] = lambda: None
        response = await client.get("/api/amaze/feeds", headers=STEWARD)
    assert response.status_code == 503
