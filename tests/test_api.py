"""API, phase 0 endpoints: health, dev users and the signed-in user."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from apps.api.deps import get_app_settings, get_db_engine
from apps.api.main import app
from ports.config import Settings

LOCAL_ADAPTERS = {
    "llm": "mock", "storage": "local", "scheduler": "local",
    "identity": "dev", "secrets": "env", "notify": "inapp",
}


@pytest.fixture
def client(migrated_engine):
    app.dependency_overrides[get_db_engine] = lambda: migrated_engine
    app.dependency_overrides[get_app_settings] = lambda: Settings(_env_file=None)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "adapters": LOCAL_ADAPTERS}


def test_health_reports_unreachable_database_with_503(client):
    dead = create_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none", connect_args={"connect_timeout": 2})
    app.dependency_overrides[get_db_engine] = lambda: dead
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["status"] == "error"
    assert response.json()["database"] == "unreachable"


def test_health_reports_missing_database_url_with_503(client):
    app.dependency_overrides[get_db_engine] = lambda: None
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["database"] == "not_configured"


def test_health_shows_selected_adapters(client):
    app.dependency_overrides[get_app_settings] = lambda: Settings(_env_file=None, llm_provider="azure_openai")
    assert client.get("/api/health").json()["adapters"]["llm"] == "azure_openai"


def test_dev_users(client):
    users = client.get("/api/dev/users").json()
    assert len(users) == 7
    assert {"id", "display_name", "email", "roles", "app"} <= set(users[0])


@pytest.mark.parametrize(("app_name", "count"), [("backend", 6), ("consumer", 1)])
def test_dev_users_by_app(client, app_name, count):
    users = client.get("/api/dev/users", params={"app": app_name}).json()
    assert len(users) == count
    assert all(u["app"] == app_name for u in users)


def test_dev_users_rejects_unknown_app(client):
    assert client.get("/api/dev/users", params={"app": "other"}).status_code == 422


def test_dev_users_hidden_unless_dev_identity(client):
    app.dependency_overrides[get_app_settings] = lambda: Settings(_env_file=None, identity_provider="entra_id")
    assert client.get("/api/dev/users").status_code == 404


def test_me_with_dev_header(client):
    response = client.get("/api/me", headers={"X-Dev-User": "dev-requester"})
    assert response.status_code == 200
    assert response.json()["display_name"] == "Riley Requester"
    assert response.json()["app"] == "consumer"


@pytest.mark.parametrize("headers", [{}, {"X-Dev-User": "dev-nobody"}])
def test_me_without_valid_user_is_401(client, headers):
    assert client.get("/api/me", headers=headers).status_code == 401


def test_scheduler_runs_with_the_api_and_has_no_jobs(client):
    scheduler = client.app.state.scheduler
    assert scheduler.name == "local"
    assert scheduler.list_jobs() == []
