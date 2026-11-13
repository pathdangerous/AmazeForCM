"""API, phase 0 endpoints: health, dev users and the signed-in user.

The app is called in-process with httpx.AsyncClient over httpx.ASGITransport; start-up and shutdown
run through app.router.lifespan_context. No Starlette TestClient (see test_no_starlette_testclient.py).
"""

from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import create_engine

from apps.api.deps import get_app_settings, get_db_engine
from apps.api.main import app
from ports.config import Settings, get_settings
from ports.db.session import get_engine

pytestmark = pytest.mark.anyio

LOCAL_ADAPTERS = {
    "llm": "mock", "storage": "local", "scheduler": "local",
    "identity": "dev", "secrets": "env", "notify": "inapp",
}

# (adapter name in /api/health, selector, stub value)
STUBS = [
    ("llm", "LLM_PROVIDER", "azure_openai"),
    ("storage", "STORAGE_PROVIDER", "azure_blob"),
    ("scheduler", "SCHEDULER_PROVIDER", "azure_functions"),
    ("identity", "IDENTITY_PROVIDER", "entra_id"),
    ("secrets", "SECRETS_PROVIDER", "key_vault"),
    ("notify", "NOTIFY_PROVIDER", "email"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def running_app(engine=None, settings=None):
    """Start the app (lifespan included) with optional overrides, and yield an httpx client."""
    if engine is not None:
        app.dependency_overrides[get_db_engine] = lambda: engine
    if settings is not None:
        app.dependency_overrides[get_app_settings] = lambda: settings
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
                yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def client(migrated_engine):
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None)) as test_client:
        yield test_client


async def test_health_ok(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "adapters": LOCAL_ADAPTERS, "unavailable": []}


@pytest.mark.parametrize(("adapter", "selector", "value"), STUBS, ids=[s[0] for s in STUBS])
async def test_stub_adapter_gives_503_never_500(monkeypatch, migrated_engine, adapter, selector, value):
    """Start-up, health and /api/me with one Azure stub selected through the environment."""
    for _, other, _ in STUBS:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setenv(selector, value)
    get_settings.cache_clear()
    get_engine.cache_clear()
    try:
        # With the secrets stub the real engine lookup must run, to prove it cannot fail with a 500.
        async with running_app(engine=None if adapter == "secrets" else migrated_engine) as client:
            health = await client.get("/api/health")
            assert health.status_code == 503
            body = health.json()
            assert body["status"] == "error"
            assert body["unavailable"] == [adapter]
            assert body["adapters"][adapter] == value
            assert body["database"] == ("not_configured" if adapter == "secrets" else "ok")

            me = await client.get("/api/me", headers={"X-Dev-User": "dev-checker"})
            if adapter == "identity":
                assert me.status_code == 503
                assert "implement after move" in me.json()["detail"]
            else:
                assert me.status_code == 200
    finally:
        get_settings.cache_clear()
        get_engine.cache_clear()


async def test_health_reports_unreachable_database_with_503(client):
    dead = create_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none", connect_args={"connect_timeout": 2})
    app.dependency_overrides[get_db_engine] = lambda: dead
    response = await client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["status"] == "error"
    assert response.json()["database"] == "unreachable"


async def test_health_reports_missing_database_url_with_503(client):
    app.dependency_overrides[get_db_engine] = lambda: None
    response = await client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["database"] == "not_configured"


async def test_health_shows_selected_adapters(client):
    app.dependency_overrides[get_app_settings] = lambda: Settings(_env_file=None, llm_provider="azure_openai")
    assert (await client.get("/api/health")).json()["adapters"]["llm"] == "azure_openai"


async def test_dev_users(client):
    users = (await client.get("/api/dev/users")).json()
    assert len(users) == 7
    assert {"id", "display_name", "email", "roles", "app"} <= set(users[0])


@pytest.mark.parametrize(("app_name", "count"), [("backend", 6), ("consumer", 1)])
async def test_dev_users_by_app(client, app_name, count):
    users = (await client.get("/api/dev/users", params={"app": app_name})).json()
    assert len(users) == count
    assert all(u["app"] == app_name for u in users)


async def test_dev_users_rejects_unknown_app(client):
    assert (await client.get("/api/dev/users", params={"app": "other"})).status_code == 422


async def test_dev_users_hidden_unless_dev_identity(client):
    app.dependency_overrides[get_app_settings] = lambda: Settings(_env_file=None, identity_provider="entra_id")
    assert (await client.get("/api/dev/users")).status_code == 404


async def test_me_with_dev_header(client):
    response = await client.get("/api/me", headers={"X-Dev-User": "dev-requester"})
    assert response.status_code == 200
    assert response.json()["display_name"] == "Riley Requester"
    assert response.json()["app"] == "consumer"


@pytest.mark.parametrize("headers", [{}, {"X-Dev-User": "dev-nobody"}])
async def test_me_without_valid_user_is_401(client, headers):
    assert (await client.get("/api/me", headers=headers)).status_code == 401


async def test_scheduler_runs_with_the_api_and_has_no_jobs(client):
    scheduler = app.state.scheduler
    assert scheduler.name == "local"
    assert scheduler.list_jobs() == []
