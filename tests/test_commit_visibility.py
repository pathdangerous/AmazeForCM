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
