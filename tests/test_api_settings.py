"""Phase 7.18, T3: GET /api/amaze/settings, read only: each agent, its provider (mock, azure_openai or external) and whether
EXTERNAL_AGENT_FOR is set. No secret value is ever in the body."""

import pytest

from agents.registry import AGENT_PROMPTS
from ports.config import Settings
from tests.conftest import running_app

pytestmark = pytest.mark.anyio
OPERATOR = {"X-Dev-User": "dev-operator"}


async def _get(db_session, migrated_engine, **settings):
    async with running_app(engine=migrated_engine, session=db_session, settings=Settings(_env_file=None, **settings)) as c:
        return await c.get("/api/amaze/settings", headers=OPERATOR)


async def test_every_registered_agent_is_listed_with_the_model_provider_by_default(db_session, migrated_engine):
    response = await _get(db_session, migrated_engine)
    assert response.status_code == 200
    body = response.json()
    assert [a["name"] for a in body["agents"]] == sorted(AGENT_PROMPTS)
    assert {a["provider"] for a in body["agents"]} == {"mock"} and not any(a["external"] for a in body["agents"])
    assert (body["llm_provider"], body["external_agent_provider"], body["external_agent_for"]) == ("mock", "none", "")
    assert (body["external_agent_set"], body["external_agent_active"]) == (False, False)
    mapping = next(a for a in body["agents"] if a["name"] == "MappingAgent")
    assert (mapping["autonomy"], mapping["approver_roles"]) == ("A2", ["data_steward"])


async def test_the_azure_openai_provider_is_named_for_every_agent(db_session, migrated_engine):
    body = (await _get(db_session, migrated_engine, llm_provider="azure_openai")).json()
    assert {a["provider"] for a in body["agents"]} == {"azure_openai"}


async def test_the_one_routed_agent_shows_external(db_session, migrated_engine):
    body = (await _get(db_session, migrated_engine, external_agent_provider="hexaware",
                       external_agent_for="MappingAgent")).json()
    assert [(a["name"], a["provider"], a["external"]) for a in body["agents"] if a["external"]] == [
        ("MappingAgent", "external", True)]
    assert {a["provider"] for a in body["agents"] if a["name"] != "MappingAgent"} == {"mock"}
    assert (body["external_agent_set"], body["external_agent_active"], body["external_agent_known"]) == (True, True, True)


async def test_a_name_set_with_provider_none_is_not_active_and_a_wrong_name_is_not_known(db_session, migrated_engine):
    inactive = (await _get(db_session, migrated_engine, external_agent_for="MappingAgent")).json()
    assert (inactive["external_agent_set"], inactive["external_agent_active"]) == (True, False)
    assert not any(a["external"] for a in inactive["agents"])
    unknown = (await _get(db_session, migrated_engine, external_agent_provider="hexaware",
                          external_agent_for="NoSuchAgent")).json()
    assert (unknown["external_agent_set"], unknown["external_agent_known"]) == (True, False)
    assert not any(a["external"] for a in unknown["agents"])


async def test_no_secret_value_is_in_the_body(db_session, migrated_engine, monkeypatch):
    for key in ("EXTERNAL_AGENT_API_KEY", "EXTERNAL_AGENT_BASE_URL", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.setenv(key, f"secret-{key}")
    response = await _get(db_session, migrated_engine, external_agent_provider="hexaware", external_agent_for="MappingAgent")
    assert "secret-" not in response.text


@pytest.mark.parametrize(("headers", "status"), [({}, 401), ({"X-Dev-User": "dev-requester"}, 403)])
async def test_settings_is_backend_only(db_session, migrated_engine, headers, status):
    async with running_app(engine=migrated_engine, session=db_session, settings=Settings(_env_file=None)) as c:
        assert (await c.get("/api/amaze/settings", headers=headers)).status_code == status
