"""Commit visibility: the work of "Find a source" and of the daily job is really committed.

Closes phase 3 plan, risk 11. The other API tests run every request on the test's one rolled-back
session, so a missing session.commit() would go unnoticed there. These tests:
- use the app's real session factories (get_db_session for the API, ports.db.get_sessionmaker for the
  job) on amaze_test;
- read the result from a separate connection, which sees only committed rows.

Rule (2026-11-14): one test per endpoint (or job) that commits, happy path only. Negative outcomes
stay in the shared-session tests. Cost: about 1.0 to 1.5 s per test, mostly the schema below.

Each test gets its own schema in amaze_test, migrated from scratch and dropped afterwards. Rows are
really committed, and the append-only tables refuse DELETE, so cleaning up row by row would mean
working around the triggers; dropping the whole schema does not (the test fixture does the same to
public on every run).
"""

import uuid

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from apps.api.jobs import DailyFetch
from core.repositories.requirements import RequirementRepository
from core.seed import seed_glossary
from core.steps.known_source import find_known_source
from ports.config import Settings
from ports.connectors import get_connector
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from ports.db import get_sessionmaker
from ports.storage.local import LocalBlobStore
from tests.conftest import alembic_config, running_app

ONBOARDING = "Your data is getting onboarded; we will notify you once it is ready."


@pytest.fixture
def own_schema(test_database_url):
    """An engine on a new schema of amaze_test, migrated to head; the schema is dropped afterwards."""
    schema = f"commit_check_{uuid.uuid4().hex[:8]}"
    base = create_engine(test_database_url)
    with base.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    url = make_url(test_database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    url_text = url.render_as_string(hide_password=False)
    engine = create_engine(url_text)
    try:
        command.upgrade(alembic_config(url_text), "head")
        with get_sessionmaker(engine)() as session:
            seed_glossary(session)
            session.commit()
        yield engine
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        base.dispose()


def committed(engine, sql, **params):
    """Read on a separate connection: only committed rows are visible here."""
    with engine.connect() as connection:
        return connection.execute(text(sql), params).all()


@pytest.mark.anyio
async def test_find_source_commits_the_selection_and_the_fetch(own_schema, tmp_path):
    from apps.api.deps import get_blob_store_port
    from apps.api.main import app

    blobs = LocalBlobStore(tmp_path / "blob")
    headers = {"X-Dev-User": "dev-requester"}
    async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:  # no session override
        app.dependency_overrides[get_blob_store_port] = lambda: blobs
        raised = await client.post("/api/consumer/requests", headers=headers,
                                   json={"query": "volume", "element_keys": ["volume"]})
        requirement_id = raised.json()["requirement"]["requirement"]["id"]
        found = await client.post(f"/api/consumer/requirements/{requirement_id}/find-source", headers=headers)
        assert found.status_code == 200, found.text
        assert (found.json()["check"]["outcome"], found.json()["fetch"]["outcome"]) == ("known_source", "fetched")

    assert committed(own_schema, "SELECT status FROM requirements WHERE id = :id", id=requirement_id) == [
        ("onboarding",)
    ]
    assert committed(own_schema, "SELECT source_code, basis FROM source_choices WHERE requirement_id = :id",
                     id=requirement_id) == [("syn_exchange_volumes", "known_source")]
    assert committed(own_schema, "SELECT f.code, s.outcome FROM source_fetches s JOIN feeds f ON f.id = s.feed_id") \
        == [("syn_exchange_volumes", "fetched")]
    assert committed(own_schema, "SELECT subject FROM notifications WHERE subject LIKE 'File ready%'") == [
        ("File ready to profile: syn_exchange_volumes",)
    ]


# ---------------------------------------------------------------- 3b: one test per endpoint (assumption 57)

RILEY = {"X-Dev-User": "dev-requester"}
BLAKE = {"X-Dev-User": "dev-backend-team"}


class Flow:
    """Drives the consumer API on the app's real session factory, one step at a time."""

    def __init__(self, client):
        self.client = client

    async def post(self, path, body=None):
        response = await self.client.post(path, headers=RILEY, json=body if body is not None else {})
        assert response.status_code in (200, 201), response.text
        return response.json()

    async def raised(self, query="open price", keys=("open_price",)):
        body = await self.post("/api/consumer/requests", {"query": query, "element_keys": list(keys)})
        self.id = body["requirement"]["requirement"]["id"]
        return self.id

    async def step(self, action, body=None):
        return await self.post(f"/api/consumer/requirements/{self.id}/{action}", body)

    async def listed(self, query="open price"):
        await self.raised(query)
        await self.step("find-source")
        await self.step("scope/decision", {"decision": "approved"})
        listed = await self.step("sources")
        return {c["source"]["code"]: c for c in listed["result"]["candidates"]}

    async def amaze(self, feed_code, action, body=None):
        """A phase 4 step on the feed of this source, as Blake Backend."""
        feeds = (await self.client.get("/api/amaze/feeds", headers=BLAKE)).json()
        self.feed_id = next(f["id"] for f in feeds if f["code"] == feed_code)
        response = await self.client.post(f"/api/amaze/feeds/{self.feed_id}/{action}", headers=BLAKE, json=body or {})
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def flow(own_schema, tmp_path):
    from contextlib import asynccontextmanager

    from apps.api.deps import get_blob_store_port
    from apps.api.main import app

    blobs = LocalBlobStore(tmp_path / "blob")

    @asynccontextmanager
    async def _flow():
        async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:  # no session override
            app.dependency_overrides[get_blob_store_port] = lambda: blobs
            yield Flow(client)

    return _flow


def _status(engine, requirement_id):
    return committed(engine, "SELECT status FROM requirements WHERE id = :id", id=requirement_id)


@pytest.mark.anyio
async def test_scope_commits_the_proposal(own_schema, flow):
    async with flow() as f:
        await f.raised("yield to maturity", ("yield_to_maturity",))
        await f.step("find-source")
        await f.step("scope/decision", {"decision": "rejected", "note": "internal"})
        before = committed(own_schema, "SELECT count(*) FROM proposal_log")[0][0]
        await f.step("scope", {"note": "internal desk data"})
    assert committed(own_schema, "SELECT count(*) FROM proposal_log")[0][0] == before + 1
    assert committed(own_schema, "SELECT kind, status, payload->>'visibility' FROM proposals "
                                 "WHERE requirement_id = :id ORDER BY created_at", id=f.id) == [
        ("scope", "rejected", "public"), ("scope", "pending", "private")]
    assert _status(own_schema, f.id) == [("awaiting_scope_confirmation",)]


@pytest.mark.anyio
async def test_scope_decision_commits_the_scope(own_schema, flow):
    async with flow() as f:
        await f.raised()
        await f.step("find-source")
        await f.step("scope/decision", {"decision": "approved"})
    assert committed(own_schema, "SELECT a.decision, a.decided_by FROM approvals a JOIN proposals p "
                                 "ON p.id = a.proposal_id WHERE p.requirement_id = :id", id=f.id) == [
        ("approved", "dev-requester")]
    assert committed(own_schema, "SELECT version, visibility FROM requirement_scopes WHERE requirement_id = :id",
                     id=f.id) == [(1, "public")]
    assert _status(own_schema, f.id) == [("scope_confirmed",)]


@pytest.mark.anyio
async def test_sources_commits_one_proposal_per_source(own_schema, flow):
    async with flow() as f:
        listed = await f.listed()
    assert sorted(listed) == ["syn_backup_eod_prices", "syn_vendor_eod_prices"]
    assert committed(own_schema, "SELECT count(*), count(DISTINCT proposal_log_id) FROM proposals "
                                 "WHERE requirement_id = :id AND kind = 'source_list'", id=f.id) == [(2, 1)]
    assert committed(own_schema, "SELECT agent_name FROM proposal_log ORDER BY created_at") == [
        ("IntentAgent",), ("SourceDiscoveryAgent",)]
    assert _status(own_schema, f.id) == [("awaiting_source_choice",)]


@pytest.mark.anyio
async def test_pick_commits_the_choice_and_the_fetch(own_schema, flow):
    async with flow() as f:
        vendor = (await f.listed())["syn_vendor_eod_prices"]
        picked = await f.step("pick", {"proposal_id": vendor["proposal_id"],
                                       "accepted_conditions": vendor["source"]["conditions"]})
        assert picked["fetch"]["outcome"] == "fetched"
    assert committed(own_schema, "SELECT a.decision FROM approvals a WHERE a.proposal_id = :p",
                     p=vendor["proposal_id"]) == [("approved",)]
    assert committed(own_schema, "SELECT version, basis, accepted_conditions->>'licence' FROM source_choices "
                                 "WHERE requirement_id = :id", id=f.id) == [
        (1, "requester_pick", vendor["source"]["conditions"]["licence"])]
    assert committed(own_schema, "SELECT f.code, s.outcome FROM source_fetches s JOIN feeds f ON f.id = s.feed_id") \
        == [("syn_vendor_eod_prices", "fetched")]
    assert _status(own_schema, f.id) == [("onboarding",)]


@pytest.mark.anyio
async def test_decline_commits_the_rejection(own_schema, flow):
    async with flow() as f:
        vendor = (await f.listed())["syn_vendor_eod_prices"]
        await f.step("decline", {"proposal_id": vendor["proposal_id"], "note": "No vendor agreement"})
    assert committed(own_schema, "SELECT p.status, a.decision, a.note FROM proposals p JOIN approvals a "
                                 "ON a.proposal_id = p.id WHERE p.id = :p", p=vendor["proposal_id"]) == [
        ("rejected", "rejected", "No vendor agreement")]


@pytest.mark.anyio
async def test_release_commits_the_released_row(own_schema, flow):
    async with flow() as f:
        await f.raised("accrued interest", ("accrued_interest",))
        await f.step("find-source")  # known source, source_down
        await f.step("release")
    assert committed(own_schema, "SELECT version, action, reason, decided_by FROM source_choices "
                                 "WHERE requirement_id = :id ORDER BY version", id=f.id) == [
        (1, "selected", None, None), (2, "released", "requester_asked", "dev-requester")]
    assert _status(own_schema, f.id) == [("no_source_found",)]


@pytest.mark.anyio
async def test_assist_commits_the_log_row(own_schema, flow):
    async with flow() as f:
        body = await f.post("/api/consumer/search/assist", {"q": "closing prices"})
        assert [s["element_key"] for s in body["suggestions"]] == ["close_price"]
    assert committed(own_schema, "SELECT agent_name, prompt_id FROM proposal_log") == [
        ("DiscoveryAssistant", "discovery_assistant")]
    assert committed(own_schema, "SELECT count(*) FROM proposals") == [(0,)]


# ---------------------------------------------------------------- phase 4: one test per commit (plan, assumption 42)

VOLUMES = "syn_exchange_volumes"


async def _volumes_onboarding(f):
    await f.raised("volume", ("volume",))
    await f.step("find-source")


async def _accepted_and_registered(f, source=VOLUMES):
    for action, body in (("profile", {}), ("profile/summary", {}), ("signoff", {"outcome": "meets"}),
                         ("register", {"owner_id": "dev-data-owner", "visibility": "open"})):
        await f.amaze(source, action, body)


@pytest.mark.anyio
async def test_profile_commits_the_profile(own_schema, flow):
    async with flow() as f:
        await _volumes_onboarding(f)
        await f.amaze(VOLUMES, "profile")
    assert committed(own_schema, "SELECT version, outcome, row_count, profiled_by FROM feed_profiles") == [
        (1, "profiled", 10, "dev-backend-team")]


@pytest.mark.anyio
async def test_summary_commits_the_proposal(own_schema, flow):
    async with flow() as f:
        await _volumes_onboarding(f)
        await f.amaze(VOLUMES, "profile")
        await f.amaze(VOLUMES, "profile/summary")
    assert committed(own_schema, "SELECT agent_name FROM proposal_log WHERE agent_name = 'ProfilerAgent'") == [
        ("ProfilerAgent",)]
    assert committed(own_schema, "SELECT kind, status, payload->>'recommended_outcome' FROM proposals "
                                 "WHERE feed_id = :f", f=f.feed_id) == [("profile_summary", "pending", "meets")]


@pytest.mark.anyio
async def test_signoff_commits_the_decision(own_schema, flow):
    async with flow() as f:
        await _volumes_onboarding(f)
        await f.amaze(VOLUMES, "profile")
        await f.amaze(VOLUMES, "profile/summary")
        await f.amaze(VOLUMES, "signoff", {"outcome": "meets"})
    assert committed(own_schema, "SELECT a.decision, a.decided_by FROM approvals a JOIN proposals p "
                                 "ON p.id = a.proposal_id WHERE p.kind = 'profile_summary'") == [
        ("approved", "dev-backend-team")]
    assert committed(own_schema, "SELECT version, outcome, signed_off_by FROM feed_signoffs") == [
        (1, "meets", "dev-backend-team")]


@pytest.mark.anyio
async def test_register_commits_the_registration(own_schema, flow):
    async with flow() as f:
        await _volumes_onboarding(f)
        await _accepted_and_registered(f)
    assert committed(own_schema, "SELECT version, owner_id, visibility FROM feed_registrations") == [
        (1, "dev-data-owner", "open")]
    assert committed(own_schema, "SELECT stage, owner_id FROM feeds WHERE code = :c", c=VOLUMES) == [
        ("registered", "dev-data-owner")]


@pytest.mark.anyio
async def test_find_source_commits_the_link_to_a_registered_feed(own_schema, flow):
    async with flow() as f:
        await _volumes_onboarding(f)
        await _accepted_and_registered(f)
        fetches = committed(own_schema, "SELECT count(*) FROM source_fetches")
        await f.raised("volume again", ("volume",))
        linked = await f.step("find-source")
        assert linked["registration"]["outcome"] == "duplicate_linked"
    assert _status(own_schema, f.id) == [("onboarding",)]
    assert committed(own_schema, "SELECT count(*) FROM source_fetches") == fetches


@pytest.mark.anyio
async def test_pick_commits_the_link_to_a_registered_feed(own_schema, flow):
    vendor_code = "syn_vendor_eod_prices"
    async with flow() as f:
        vendor = (await f.listed())[vendor_code]
        await f.step("pick", {"proposal_id": vendor["proposal_id"], "accepted_conditions": vendor["source"]["conditions"]})
        await _accepted_and_registered(f, vendor_code)
        fetches = committed(own_schema, "SELECT count(*) FROM source_fetches")
        vendor = (await f.listed("open price again"))[vendor_code]
        picked = await f.step("pick", {"proposal_id": vendor["proposal_id"],
                                       "accepted_conditions": vendor["source"]["conditions"]})
        assert picked["registration"]["outcome"] == "duplicate_linked"
    assert committed(own_schema, "SELECT a.decision FROM approvals a WHERE a.proposal_id = :p",
                     p=vendor["proposal_id"]) == [("approved",)]
    assert committed(own_schema, "SELECT version, basis FROM source_choices WHERE requirement_id = :id",
                     id=f.id) == [(1, "requester_pick")]
    assert _status(own_schema, f.id) == [("onboarding",)]
    assert committed(own_schema, "SELECT count(*) FROM source_fetches") == fetches


def test_the_daily_job_commits_each_fetch(own_schema, tmp_path):
    sessions = get_sessionmaker(own_schema)  # the app's real session factory, on amaze_test
    with sessions() as session:
        requirement = RequirementRepository(session).create(requester_id="dev-requester", title="volume",
                                                            element_keys=["volume"])
        find_known_source(session, requirement_id=requirement.id, catalogue=SourceCatalogue.load(DEFAULT_CATALOGUE))
        session.commit()

    settings = Settings(_env_file=None)
    daily = DailyFetch(session_factory=sessions, blob_store=LocalBlobStore(tmp_path / "blob"),
                       connector_factory=lambda name: get_connector(name, settings),
                       backend_team=lambda: ["dev-backend-team"])
    assert daily.run() == {"syn_exchange_volumes": "fetched"}

    assert committed(own_schema, "SELECT status FROM requirements WHERE id = :id", id=requirement.id) == [
        ("onboarding",)
    ]
    assert committed(own_schema, "SELECT outcome FROM source_fetches") == [("fetched",)]
    assert committed(own_schema, "SELECT count(*) FROM notifications WHERE subject LIKE 'File ready%'") == [(1,)]
