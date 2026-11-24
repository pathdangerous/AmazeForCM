"""API, phase 0 endpoints: health, dev users and the signed-in user.

The app is called in-process with httpx.AsyncClient over httpx.ASGITransport; start-up and shutdown
run through app.router.lifespan_context. No Starlette TestClient (see test_no_starlette_testclient.py).
"""

import pytest
from sqlalchemy import create_engine

from apps.api.deps import get_app_settings, get_db_engine
from apps.api.main import app
from ports.config import Settings, get_settings
from ports.db.session import get_engine
from tests.conftest import running_app

pytestmark = pytest.mark.anyio

LOCAL_ADAPTERS = {
    "llm": "mock", "storage": "local", "scheduler": "local",
    "identity": "dev", "secrets": "env", "notify": "inapp", "pipeline": "local",
}

# (adapter name in /api/health, selector, stub value)
STUBS = [
    ("llm", "LLM_PROVIDER", "azure_openai"),
    ("storage", "STORAGE_PROVIDER", "azure_blob"),
    ("scheduler", "SCHEDULER_PROVIDER", "azure_functions"),
    ("identity", "IDENTITY_PROVIDER", "entra_id"),
    ("secrets", "SECRETS_PROVIDER", "key_vault"),
    ("notify", "NOTIFY_PROVIDER", "email"),
    ("pipeline", "PIPELINE_PROVIDER", "azure_devops"),
]


@pytest.fixture
async def client(migrated_engine):
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None)) as test_client:
        yield test_client


async def test_health_ok(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "adapters": LOCAL_ADAPTERS, "unavailable": [],
                               "four_eyes": True}  # phase 7.8: no settings row means four-eyes on


@pytest.mark.parametrize(("adapter", "selector", "value"), STUBS, ids=[s[0] for s in STUBS])
async def test_stub_adapter_gives_503_never_500(monkeypatch, migrated_engine, adapter, selector, value, azure_env_only):
    """Start-up, health and /api/me with one Azure adapter selected through the environment (the LLM one is
    unconfigured here, so it is unavailable; azure_env_only keeps this machine's .env out)."""
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


AZURE_KEYS = {
    "AZURE_OPENAI_ENDPOINT": "https://fake-endpoint-xyz.example.invalid",
    "AZURE_OPENAI_DEPLOYMENT": "main-deploy",
    "AZURE_OPENAI_API_VERSION": "2026-01-01",
    "AZURE_OPENAI_API_KEY": "fake-key-0123456789abcdef",
}


@pytest.fixture
def azure_env_only(monkeypatch, tmp_path):
    """The adapter reads only the process environment: never this machine's .env (phase 7.8, task 1.2)."""
    import ports.llm.azure_openai as module

    real = module.read_env
    monkeypatch.setattr(module, "read_env", lambda keys=None, *, env_file=None, **kw: real(
        keys, env_file=tmp_path / "no-such.env", **kw))
    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_SMALL",
                "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_AUTH", "AZURE_OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)


async def test_health_azure_openai_configured_is_available_and_makes_no_model_call(client, monkeypatch, azure_env_only):
    for key, value in AZURE_KEYS.items():
        monkeypatch.setenv(key, value)
    app.dependency_overrides[get_app_settings] = lambda: Settings(_env_file=None, llm_provider="azure_openai")
    response = await client.get("/api/health")
    assert (response.status_code, response.json()["unavailable"]) == (200, [])
    assert response.json()["adapters"]["llm"] == "azure_openai"


async def test_health_azure_openai_not_configured_is_unavailable(client, azure_env_only):
    app.dependency_overrides[get_app_settings] = lambda: Settings(_env_file=None, llm_provider="azure_openai")
    response = await client.get("/api/health")
    assert (response.status_code, response.json()["unavailable"]) == (503, ["llm"])


async def test_a_refused_model_call_gives_422_with_its_reason(client):
    from apps.api.deps import get_llm_provider_port
    from ports.llm.base import LLMRefused

    class Refusing:
        name = "mock"

        def complete(self, **kwargs):
            raise LLMRefused("content_filter", "synthetic")

    app.dependency_overrides[get_llm_provider_port] = lambda: Refusing()
    response = await client.post("/api/consumer/search/assist", headers={"X-Dev-User": "dev-requester"},
                                 json={"q": "closing prices"})
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "The model refused to answer this input.", "reason": "content_filter"}


async def test_dev_users(client):
    users = (await client.get("/api/dev/users")).json()
    assert len(users) == 8  # phase 7.8: dev-operator
    assert {"id", "display_name", "email", "roles", "app"} <= set(users[0])


@pytest.mark.parametrize(("app_name", "count"), [("backend", 7), ("consumer", 1)])
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


async def test_scheduler_runs_with_the_api_and_holds_the_daily_fetch_and_the_report_job(client):
    """Phase 0 started with no jobs; phase 3 registers the daily fetch and phase 7.15 the report job at every start-up."""
    scheduler = app.state.scheduler
    assert scheduler.name == "local"
    assert sorted(job.job_id for job in scheduler.list_jobs()) == ["daily-source-fetch", "reports"]
