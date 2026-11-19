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
from sqlalchemy import text

from apps.api.jobs import DailyFetch
from core.repositories.requirements import RequirementRepository
from core.seed import seed_glossary
from core.steps.known_source import find_known_source
from ports.config import Settings
from ports.connectors import get_connector
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from ports.db import get_sessionmaker
from ports.storage.local import LocalBlobStore
from tests.conftest import alembic_config, fresh_schema, replay_ddl, running_app

ONBOARDING = "Your data is getting onboarded; we will notify you once it is ready."


@pytest.fixture
def own_schema(test_database_url, offline_ddl):
    """An engine on a new schema of amaze_test, built at head by replaying the DDL of `alembic upgrade head`
    (conftest.offline_ddl; phase 6 plan, task 3), the glossary seeded; the schema is dropped afterwards."""
    with fresh_schema(test_database_url, "commit_check") as (engine, _url, _schema):
        replay_ddl(engine, offline_ddl)
        with get_sessionmaker(engine)() as session:
            seed_glossary(session)
            session.commit()
        yield engine


def _shape(engine, schema):
    """Everything a migration builds in the schema, as sorted lists: columns, constraints, indexes, triggers
    (with their definitions), functions (with their bodies), views. The schema's own name is masked."""
    queries = {
        "columns": "SELECT table_name, ordinal_position, column_name, data_type, udt_name, is_nullable, "
                   "column_default FROM information_schema.columns WHERE table_schema = :s",
        "constraints": "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
                       "WHERE connamespace = CAST(:s AS regnamespace)",
        "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = :s",
        "triggers": "SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid WHERE c.relnamespace = CAST(:s AS regnamespace) "
                    "AND NOT t.tgisinternal",
        "functions": "SELECT proname, prosrc FROM pg_proc WHERE pronamespace = CAST(:s AS regnamespace)",
        "views": "SELECT viewname, definition FROM pg_views WHERE schemaname = :s",
    }
    with engine.connect() as connection:
        return {name: sorted(tuple(str(v).replace(schema, "<schema>") for v in row)
                             for row in connection.execute(text(sql), {"s": schema}))
                for name, sql in queries.items()}


def _row_counts(engine, schema):
    with engine.connect() as connection:
        tables = connection.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = :s AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"), {"s": schema}).scalars().all()
        return {t: connection.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tables}


def test_the_offline_ddl_schema_equals_the_alembic_schema(test_database_url, offline_ddl):
    """Phase 6 plan, task 3: the schema-per-test cases are built from the offline DDL, not by `alembic upgrade
    head`. Both are built here; every table, column, constraint, index, trigger, function and view must be the
    same, and every table must hold the same number of rows (run with -s to see the counts side by side)."""
    with (fresh_schema(test_database_url, "offline_check") as (offline, _o_url, o_schema),
          fresh_schema(test_database_url, "alembic_check") as (upgraded, u_url, u_schema)):
        replay_ddl(offline, offline_ddl)
        command.upgrade(alembic_config(u_url), "head")
        offline_counts, upgraded_counts = _row_counts(offline, o_schema), _row_counts(upgraded, u_schema)
        print(f"\n{'table':<28}{'offline DDL':>12}{'alembic':>10}")
        for table in sorted(set(offline_counts) | set(upgraded_counts)):
            print(f"{table:<28}{offline_counts.get(table, '-'):>12}{upgraded_counts.get(table, '-'):>10}")
        assert offline_counts == upgraded_counts
        assert _shape(offline, o_schema) == _shape(upgraded, u_schema)


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
    ] * 2  # the backend team is dev-backend-team and, since 7.8, dev-operator


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


# ---------------------------------------------------------------- phase 5: one test per commit (plan, assumption 45)

GLENN = {"X-Dev-User": "dev-glossary-owner"}
SAM = {"X-Dev-User": "dev-data-steward"}


def bond_feed(engine, code="syn_bond_terms") -> str:
    """A feed registered with the bond profile (seven columns), committed; returns its id."""
    from core.repositories.feeds import FeedRepository
    from tests.onboarded import registered_feed
    from tests.test_s05_glossary import BOND_COLUMNS, BOND_SPEC

    with get_sessionmaker(engine)() as session:
        feed = FeedRepository(session).create_candidate(code=code, name="Bond terms")
        feed = registered_feed(session, feed.id, spec=BOND_SPEC, columns=BOND_COLUMNS)
        session.commit()
    return str(feed.id)


def glossary_worker(engine, tmp_path, provider=None, lease_seconds=300):
    """The step 05 worker on the app's real session factory (assumption 48: the test runs it, not the app)."""
    from agents.glossary_builder import GlossaryBuilderAgent
    from apps.api.jobs import GlossaryRuns
    from ports.llm.mock import MockProvider

    return GlossaryRuns(session_factory=get_sessionmaker(engine),
                        agent_factory=lambda: GlossaryBuilderAgent(provider=provider or MockProvider(),
                                                                   blob_store=LocalBlobStore(tmp_path / "blob")),
                        owners=lambda: ["dev-glossary-owner"], lease_seconds=lease_seconds)


def queued_run(engine, feed_id, reason="requested"):
    from core.steps.s05_glossary import queue_run

    with get_sessionmaker(engine)() as session:
        run, _ = queue_run(session, feed_id=uuid.UUID(feed_id), reason=reason, requested_by="dev-glossary-owner")
        session.commit()
    return str(run.id)


async def amaze_post(client, path, body=None, headers=SAM):
    response = await client.post(f"/api/amaze/{path}", headers=headers, json=body if body is not None else {})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.anyio
async def test_register_commits_the_glossary_run(own_schema, flow):
    async with flow() as f:
        await _volumes_onboarding(f)
        await _accepted_and_registered(f)
    assert committed(own_schema, "SELECT r.reason, r.status, r.requested_by FROM glossary_runs r "
                                 "JOIN feeds f ON f.id = r.feed_id WHERE f.code = :c", c=VOLUMES) == [
        ("registered", "queued", "dev-backend-team")]


def test_the_glossary_job_commits_its_proposals(own_schema, tmp_path):
    run_id = queued_run(own_schema, bond_feed(own_schema))
    assert glossary_worker(own_schema, tmp_path).drain() == {uuid.UUID(run_id): "done"}
    assert committed(own_schema, "SELECT status, results->>'rating' FROM glossary_runs WHERE id = :id",
                     id=run_id) == [("done", "proposed")]
    assert committed(own_schema, "SELECT agent_name FROM proposal_log WHERE agent_name <> 'ProfilerAgent'") == [
        ("GlossaryBuilderAgent",)]  # registered_feed's summary is a ProfilerAgent call
    assert committed(own_schema, "SELECT payload->>'source_field', status FROM proposals "
                                 "WHERE kind = 'glossary_element' ORDER BY 1") == [
        ("rating", "pending"), ("venue_mic_code", "pending")]
    assert committed(own_schema, "SELECT recipient FROM notifications WHERE subject LIKE 'Glossary proposals%'") == [
        ("dev-glossary-owner",)]


@pytest.mark.anyio
async def test_glossary_run_commits_the_queued_run(own_schema, flow):
    feed_id = bond_feed(own_schema)
    async with flow() as f:
        body = await amaze_post(f.client, f"feeds/{feed_id}/glossary/run", headers=GLENN)
    assert committed(own_schema, "SELECT id::text, reason, status, requested_by FROM glossary_runs") == [
        (body["run"]["id"], "requested", "queued", "dev-glossary-owner")]


@pytest.mark.anyio
async def test_propose_commits_the_mapping_proposals(own_schema, flow):
    feed_id = bond_feed(own_schema)
    async with flow() as f:
        await amaze_post(f.client, f"feeds/{feed_id}/mappings/propose")
    assert committed(own_schema, "SELECT agent_name FROM proposal_log WHERE agent_name = 'MappingAgent'") == [
        ("MappingAgent",)]
    assert committed(own_schema, "SELECT payload->>'source_field' FROM proposals WHERE kind = 'mapping' "
                                 "AND status = 'pending' ORDER BY 1") == [
        ("cpn",), ("date",), ("instr_id",), ("matures_on",), ("venue_mic_code",)]
    assert committed(own_schema, "SELECT reason, columns FROM glossary_runs") == [("sent_back", ["rating", "maturity"])]


@pytest.mark.anyio
async def test_a_glossary_decision_commits_the_element(own_schema, flow, tmp_path):
    queued_run(own_schema, bond_feed(own_schema))
    glossary_worker(own_schema, tmp_path).drain()
    [(proposal_id,)] = committed(own_schema, "SELECT id::text FROM proposals WHERE payload->>'source_field' = 'rating'")
    async with flow() as f:
        await amaze_post(f.client, f"proposals/{proposal_id}/decision", {"decision": "approved"}, headers=GLENN)
    assert committed(own_schema, "SELECT decision, decided_by FROM approvals WHERE proposal_id = :p",
                     p=proposal_id) == [("approved", "dev-glossary-owner")]
    assert committed(own_schema, "SELECT key, version, created_by FROM glossary_elements WHERE proposal_id = :p",
                     p=proposal_id) == [("rating", 1, "dev-glossary-owner")]


@pytest.mark.anyio
async def test_a_mapping_decision_commits_the_mapping(own_schema, flow):
    feed_id = bond_feed(own_schema)
    async with flow() as f:
        proposed = await amaze_post(f.client, f"feeds/{feed_id}/mappings/propose")
        cpn = next(r for r in proposed["result"]["recommended"] if r["source_field"] == "cpn")
        await amaze_post(f.client, f"proposals/{cpn['proposal_id']}/decision", {"decision": "approved"})
    assert committed(own_schema, "SELECT decision, decided_by FROM approvals WHERE proposal_id = :p",
                     p=cpn["proposal_id"]) == [("approved", "dev-data-steward")]
    assert committed(own_schema, "SELECT source_field, element_key, version FROM mappings") == [("cpn", "coupon_rate", 1)]


def test_the_daily_job_commits_the_broken_catalogue_notification(own_schema, tmp_path):
    """Phase 4 plan, assumption 36: with a catalogue that fails to load, the backend team's notification is
    committed in its own transaction (independent review, finding 9)."""
    def broken():
        raise ValueError("source code 'vendor_x' must start with 'syn_'")

    DailyFetch(session_factory=get_sessionmaker(own_schema), blob_store=LocalBlobStore(tmp_path / "blob"),
               connector_factory=lambda name: get_connector(name, Settings(_env_file=None)),
               backend_team=lambda: ["dev-backend-team"], catalogue_loader=broken).run()
    assert committed(own_schema, "SELECT recipient FROM notifications "
                                 "WHERE subject = 'Source catalogue could not be read'") == [("dev-backend-team",)]


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


# ---------------------------------------------------------------- phase 6: one test per committing endpoint or job

CASEY = {"X-Dev-User": "dev-checker"}
DEVON = {"X-Dev-User": "dev-data-owner"}
OPAL = {"X-Dev-User": "dev-operations"}
VOLUME_ROWS = 10


def volumes_feed(engine, blobs, *, mapped=True) -> str:
    """syn_exchange_volumes, registered, with its three columns mapped and a fetched file of ten rows in the
    blob store, all committed; returns the feed id."""
    from core.domain.profiling import FileColumn, SourceSpec
    from core.repositories.feeds import FeedRepository
    from tests.configured import approved_mapping
    from tests.onboarded import registered_feed

    columns = [FileColumn(name=n, type=t, null_count=0, null_rate=0.0, distinct_count=VOLUME_ROWS)
               for n, t in (("instr_id", "VARCHAR"), ("trade_date", "DATE"), ("traded_qty", "BIGINT"))]
    spec = SourceSpec(source_code="syn_exchange_volumes", elements=["instrument_id", "price_date", "volume"],
                      other_fields=[], columns={"instr_id": "instrument_id", "trade_date": "price_date",
                                                "traded_qty": "volume"})
    with get_sessionmaker(engine)() as session:
        feed = FeedRepository(session).create_candidate(
            code="syn_exchange_volumes", name="Exchange daily volumes", source_code="syn_exchange_volumes",
            connector="local_file", source_ref="files/exchange_volumes.csv")
        registered_feed(session, feed.id, spec=spec, columns=columns)
        if mapped:
            for field, key in (("instr_id", "instrument_id"), ("trade_date", "price_date"),
                               ("traded_qty", "volume")):
                approved_mapping(session, feed.id, field, key)
        session.commit()
    rows = "".join(f"syn-{n:04d},2026-11-02,{1000 + n}\n" for n in range(VOLUME_ROWS))
    blobs.put(f"raw/test/{feed.id}/f.csv", ("instr_id,trade_date,traded_qty\n" + rows).encode())
    return str(feed.id)


def waiting_requirement(engine, feed_id: str, *, title="Waiting for volume") -> str:
    """An onboarding requirement for the volume element whose current choice selects the feed, committed."""
    from tests.configured import onboarding_requirement

    with get_sessionmaker(engine)() as session:
        requirement = onboarding_requirement(session, uuid.UUID(feed_id), title=title, elements=["volume"])
        session.commit()
    return str(requirement.id)


def assert_released_and_committed(engine, requirement_id: str) -> None:
    """Phase 7: after a good scheduled load, what steps 12, 15 and 20 wrote is visible to another connection."""
    from decimal import Decimal

    assert committed(engine, "SELECT arrival_outcome, content_sha256 IS NOT NULL FROM loads "
                             "WHERE purpose = 'scheduled'") == [("on_time_complete", True)]
    assert committed(engine, 'SELECT outcome, "rows", flagged_rows, trust_score FROM load_certifications') == [
        ("released", VOLUME_ROWS, 0, Decimal("100.0"))]
    assert committed(engine, "SELECT code, owner_id, visibility FROM products WHERE feed_id IS NOT NULL") == [
        ("syn_exchange_volumes", "dev-data-owner", "open")]
    assert committed(engine, "SELECT status FROM requirements WHERE id = :r", r=requirement_id) == [("ready",)]
    assert committed(engine, "SELECT count(*) FROM notifications WHERE subject = 'Ready: Waiting for volume' "
                             "AND recipient = 'dev-requester'") == [(1,)]


def daily_on(engine, blobs):
    """The daily job on the test's schema: real sessions, the sample files of the catalogue."""
    settings = Settings(_env_file=None)
    return DailyFetch(session_factory=get_sessionmaker(engine), blob_store=blobs,
                      connector_factory=lambda name: get_connector(name, settings),
                      backend_team=lambda: ["dev-backend-team"], operations=lambda: ["dev-operations"])


@pytest.fixture
def p6(own_schema, tmp_path):
    """The API on the test's schema with a mapped syn_exchange_volumes, its blob store and its daily job."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from apps.api.deps import get_blob_store_port, get_daily_fetch
    from apps.api.main import app

    blobs = LocalBlobStore(tmp_path / "blob")

    @asynccontextmanager
    async def _p6(mapped=True):
        feed = volumes_feed(own_schema, blobs, mapped=mapped)
        daily = daily_on(own_schema, blobs)
        async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
            app.dependency_overrides[get_blob_store_port] = lambda: blobs
            app.dependency_overrides[get_daily_fetch] = lambda: daily
            yield SimpleNamespace(client=client, feed=feed, blobs=blobs, daily=daily)

    return _p6


async def call(client, path, body=None, headers=BLAKE, method="POST", expect=200):
    response = await client.request(method, f"/api/amaze/{path}", headers=headers,
                                    json=body if method == "POST" else None)
    assert response.status_code == expect, response.text
    return response.json()


async def go_live(p, *, approve=True):
    """Prepare the feed's go-live as Blake and, by default, approve it as Devon; returns the proposal id."""
    prepared = await call(p.client, f"feeds/{p.feed}/go-live/summary", {})
    proposal_id = prepared["result"]["proposal_id"]
    if approve:
        await call(p.client, f"proposals/{proposal_id}/decision", {"decision": "approved"}, headers=DEVON)
    return proposal_id


@pytest.mark.anyio
async def test_rules_propose_commits_the_proposals(own_schema, p6):
    async with p6() as p:
        proposed = await call(p.client, f"feeds/{p.feed}/rules/propose", {}, headers=SAM)
        assert proposed["result"]["outcome"] == "rules_proposed"
    assert committed(own_schema, "SELECT count(*), min(status) FROM proposals WHERE kind = 'rule'") == [(4, "pending")]
    assert committed(own_schema, "SELECT count(*) FROM proposal_log WHERE agent_name = 'RuleRecommenderAgent'") == [
        (1,)]
    assert committed(own_schema, "SELECT count(*) FROM proposal_log_discards") == [(0,)]


def messy_feed(engine) -> str:
    """Phase 7.8, task 4.3: syn_messy_feed, registered, two columns mapped, one of them (instr_id) holding the
    placeholder N/A in two rows; committed. Returns the feed id."""
    from core.domain.profiling import FileColumn, SourceSpec
    from core.repositories.feeds import FeedRepository
    from tests.configured import approved_mapping
    from tests.onboarded import registered_feed

    columns = [FileColumn(name="instr_id", type="VARCHAR", null_count=0, null_rate=0.0, distinct_count=8,
                          placeholder_count=2, placeholder_values=["N/A"]),
               FileColumn(name="traded_qty", type="BIGINT", null_count=0, null_rate=0.0, distinct_count=10)]
    spec = SourceSpec(source_code="syn_messy_feed", elements=[], other_fields=[], columns={})
    with get_sessionmaker(engine)() as session:
        feed = FeedRepository(session).create_candidate(code="syn_messy_feed", name="Messy feed")
        registered_feed(session, feed.id, spec=spec, columns=columns)
        approved_mapping(session, feed.id, "instr_id", "instrument_id")
        approved_mapping(session, feed.id, "traded_qty", "volume")
        session.commit()
    return str(feed.id)


@pytest.mark.anyio
async def test_transformations_propose_commits_the_proposals(own_schema, flow):
    feed_id = messy_feed(own_schema)
    async with flow() as f:
        proposed = await amaze_post(f.client, f"feeds/{feed_id}/transformations/propose")
    assert proposed["result"]["outcome"] == "transforms_proposed"
    assert committed(own_schema, "SELECT payload->>'source_field', status, required_approver_roles::text "
                                 "FROM proposals WHERE kind = 'transformation'") == [
        ("instr_id", "pending", "{data_steward}")]
    assert committed(own_schema, "SELECT count(*) FROM proposal_log WHERE agent_name = 'TransformationAgent'") == [(1,)]
    assert committed(own_schema, "SELECT count(*) FROM proposal_log_discards") == [(0,)]


@pytest.mark.anyio
async def test_a_transformation_decision_commits_the_transformation(own_schema, flow):
    feed_id = messy_feed(own_schema)
    async with flow() as f:
        proposed = await amaze_post(f.client, f"feeds/{feed_id}/transformations/propose")
        proposal_id = proposed["result"]["proposal_ids"][0]
        await amaze_post(f.client, f"proposals/{proposal_id}/decision", {"decision": "approved"})
    assert committed(own_schema, "SELECT decision, decided_by FROM approvals WHERE proposal_id = :p",
                     p=proposal_id) == [("approved", "dev-data-steward")]
    assert committed(own_schema, "SELECT source_field, version, created_by, steps::text, proposal_id::text = :p "
                                 "FROM transformations", p=proposal_id) == [
        ("instr_id", 1, "dev-data-steward", '[{"op": "placeholder_to_null", "values": ["N/A"]}]', True)]


@pytest.mark.anyio
async def test_the_key_policy_commits_the_next_version(own_schema, flow):
    feed_id = messy_feed(own_schema)
    async with flow() as f:
        response = await f.client.put(f"/api/amaze/feeds/{feed_id}/key-policy", headers=SAM, json={
            "policy": "hold", "key_columns": ["instr_id"], "reason": "A repeated id must stop the load."})
        assert response.status_code == 200, response.text
    assert committed(own_schema, "SELECT version, policy, key_columns::text, set_by FROM feed_key_policies") == [
        (1, "hold", "{instr_id}", "dev-data-steward")]


@pytest.mark.anyio
async def test_the_rule_decision_commits_the_rule(own_schema, p6):
    async with p6() as p:
        await call(p.client, f"feeds/{p.feed}/rules/propose", {}, headers=SAM)
        [(proposal_id,)] = committed(own_schema, "SELECT id FROM proposals WHERE payload->>'rule_key' = "
                                                 "'volume_not_negative'")
        await call(p.client, f"proposals/{proposal_id}/decision", {"decision": "approved"}, headers=SAM)
        assert committed(own_schema, "SELECT count(*) FROM rules") == [(0,)]  # one approval is not a rule
        await call(p.client, f"proposals/{proposal_id}/decision", {"decision": "approved"}, headers=CASEY)
    assert committed(own_schema, "SELECT rule_key, version, proposal_id = :p FROM rules", p=proposal_id) == [
        ("volume_not_negative", 1, True)]
    assert committed(own_schema, "SELECT role, decided_by FROM approvals WHERE proposal_id = :p ORDER BY step",
                     p=proposal_id) == [("data_steward", "dev-data-steward"), ("checker", "dev-checker")]


@pytest.mark.anyio
async def test_the_go_live_summary_commits_the_test_load_and_the_proposal(own_schema, p6):
    async with p6() as p:
        prepared = await call(p.client, f"feeds/{p.feed}/go-live/summary", {})
    assert prepared["result"]["test_load"]["status"] == "loaded"
    assert committed(own_schema, "SELECT purpose, status FROM loads") == [("go_live_test", "loaded")]
    assert committed(own_schema, "SELECT count(*) FROM load_stages WHERE outcome = 'stage_completed'") == [(4,)]
    assert committed(own_schema, "SELECT count(*) FROM standard_records") == [(VOLUME_ROWS,)]
    assert committed(own_schema, "SELECT kind, status FROM proposals WHERE kind = 'review_summary'") == [
        ("review_summary", "pending")]
    assert committed(own_schema, "SELECT count(*) FROM proposal_log WHERE agent_name = 'ReviewSummariserAgent'") == [
        (1,)]


@pytest.mark.anyio
async def test_the_owners_decision_commits_the_go_live(own_schema, p6):
    async with p6() as p:
        proposal_id = await go_live(p)
    assert committed(own_schema, "SELECT version, approved_by, proposal_id = :p, hypercare_until > now()::date "
                                 "FROM feed_go_lives", p=proposal_id) == [(1, "dev-data-owner", True, True)]
    assert committed(own_schema, "SELECT role, decision FROM approvals WHERE proposal_id = :p", p=proposal_id) == [
        ("data_owner", "approved")]
    assert committed(own_schema, "SELECT count(*) FROM notifications WHERE subject LIKE 'Feed goes live%'") == [(4,)]  # 7.8: dev-operator joins both


@pytest.mark.anyio
async def test_load_now_commits_the_scheduled_load(own_schema, p6):
    async with p6() as p:
        requirement = waiting_requirement(own_schema, p.feed)
        await go_live(p)
        loaded = await call(p.client, f"feeds/{p.feed}/loads", {}, headers=OPAL)
        assert loaded["result"] == {"fetch": "fetched", "load": "released"}  # was "loaded" before phase 7
    assert committed(own_schema, "SELECT status, go_live_id IS NOT NULL FROM loads WHERE purpose = 'scheduled'") == [
        ("released", True)]
    assert committed(own_schema, "SELECT count(*) FROM load_stages s JOIN loads l ON l.id = s.load_id "
                                 "WHERE l.purpose = 'scheduled'") == [(4,)]
    assert committed(own_schema, "SELECT count(*) FROM standard_records r JOIN loads l ON l.id = r.load_id "
                                 "WHERE l.purpose = 'scheduled'") == [(VOLUME_ROWS,)]
    assert_released_and_committed(own_schema, requirement)


@pytest.mark.anyio
async def test_the_daily_job_commits_the_scheduled_load(own_schema, p6):
    import asyncio

    async with p6() as p:
        requirement = waiting_requirement(own_schema, p.feed)
        await go_live(p)
        assert await asyncio.to_thread(p.daily.run) == {"syn_exchange_volumes": "fetched"}
    assert_released_and_committed(own_schema, requirement)
    assert committed(own_schema, "SELECT status FROM loads WHERE purpose = 'scheduled'") == [("released",)]  # was loaded
    assert committed(own_schema, "SELECT count(*) FROM standard_records r JOIN loads l ON l.id = r.load_id "
                                 "WHERE l.purpose = 'scheduled'") == [(VOLUME_ROWS,)]
    assert committed(own_schema, "SELECT count(*) FROM notifications WHERE subject = 'Load loaded: "
                                 "syn_exchange_volumes'") == [(2,)]  # hypercare: the owner and operations


@pytest.mark.anyio
async def test_confirm_upload_commits_the_feed_its_fetch_its_source_and_the_notice(own_schema, tmp_path):
    """Phase 7.8, task 2.4: the confirm endpoint on the app's real session. The feed page is read afterwards on a
    new session, which also proves the catalogue dependency reads the committed upload source."""
    from apps.api.deps import get_blob_store_port
    from apps.api.main import app

    blobs = LocalBlobStore(tmp_path / "blob")
    headers = {"X-Dev-User": "dev-operator", "Content-Type": "application/octet-stream"}
    async with running_app(engine=own_schema, settings=Settings(_env_file=None, dropzone_root=tmp_path / "dz")
                           ) as client:  # no session override
        app.dependency_overrides[get_blob_store_port] = lambda: blobs
        stored = await client.post("/api/amaze/uploads", params={"filename": "prices.csv"}, headers=headers,
                                   content=b"id,name,qty\n1,a,2\n2,b,3\n")
        confirmed = await client.post(
            f"/api/amaze/uploads/{stored.json()['upload_id']}/confirm", headers={"X-Dev-User": "dev-operator"},
            json={"filename": "prices.csv", "feed_code": "upload_prices", "feed_name": "Prices", "sheet": None,
                  "header_row": None})
        assert confirmed.status_code == 200, confirmed.text
        page = await client.get(f"/api/amaze/feeds/{confirmed.json()['feed_id']}", headers={"X-Dev-User": "dev-operator"})
        assert page.status_code == 200 and page.json()["source"]["code"] == "upl_upload_prices"

    assert committed(own_schema, "SELECT code, stage, connector, source_code, source_ref FROM feeds") == [
        ("upload_prices", "candidate", "dropzone", "upl_upload_prices", "dropzone/upload_prices")]
    assert committed(own_schema, "SELECT s.outcome, s.size_bytes > 0 FROM source_fetches s") == [("fetched", True)]
    assert committed(own_schema, "SELECT code, created_by, intake_options->>'header_row' FROM upload_sources") == [
        ("upl_upload_prices", "dev-operator", "1")]
    assert committed(own_schema, "SELECT subject FROM notifications WHERE subject LIKE 'File ready%'") == [
        ("File ready to profile: upload_prices",)] * 2  # dev-backend-team and dev-operator


@pytest.mark.anyio
async def test_a_re_upload_puts_the_file_in_the_drop_folder_and_writes_no_row(
        own_schema, tmp_path):
    """Phase 7.8, task 4.4, happy path: the re-upload on the app's real session writes only a file (no row, no load)
    and the newest file of the folder is the re-uploaded one."""
    from apps.api.deps import get_blob_store_port
    from apps.api.main import app
    from ports.connectors.dropzone import DropzoneConnector

    blobs = LocalBlobStore(tmp_path / "blob")
    dropzone = tmp_path / "dz"
    operator = {"X-Dev-User": "dev-operator"}
    async with running_app(engine=own_schema, settings=Settings(_env_file=None, dropzone_root=dropzone)) as client:
        app.dependency_overrides[get_blob_store_port] = lambda: blobs
        stored = await client.post("/api/amaze/uploads", params={"filename": "prices.csv"},
                                   headers={**operator, "Content-Type": "application/octet-stream"},
                                   content=b"id,name,qty\n1,a,2\n")
        confirmed = await client.post(
            f"/api/amaze/uploads/{stored.json()['upload_id']}/confirm", headers=operator,
            json={"filename": "prices.csv", "feed_code": "upload_prices", "feed_name": "Prices", "sheet": None,
                  "header_row": None})
        again = await client.post(f"/api/amaze/feeds/{confirmed.json()['feed_id']}/files",
                                  params={"filename": "next.csv"}, headers={**operator, "Content-Type": "application/octet-stream"},
                                  content=b"id,name,qty\n2,b,3\n")
        assert again.status_code == 200, again.text
    assert committed(own_schema, "SELECT count(*) FROM loads") == [(0,)]
    assert committed(own_schema, "SELECT count(*) FROM source_fetches") == [(1,)]  # the first file only
    assert DropzoneConnector(dropzone).fetch("dropzone/upload_prices").content == b"id,name,qty\n2,b,3\n"
