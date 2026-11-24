"""Phase 7.18, T1: the ExternalAgent port. "none" is never called; "hexaware" is a stub until the API is known.
No network, no HTTP client, no adapter imported by business logic."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import BaseModel

from ports.config import Settings, read_env
from ports.external_agents import ExternalAgentUnavailable, get_external_agent, validated
from ports.external_agents.hexaware import HexawareExternalAgent

REPO_ROOT = Path(__file__).resolve().parents[1]


class Answer(BaseModel):
    text: str


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_provider_none_is_the_default_and_gives_no_adapter():
    assert _settings().external_agent_provider == "none"
    assert _settings().external_agent_for == ""
    assert get_external_agent(_settings()) is None


def test_provider_hexaware_gives_the_stub():
    adapter = get_external_agent(_settings(external_agent_provider="hexaware"))
    assert isinstance(adapter, HexawareExternalAgent)
    assert adapter.name == "hexaware" and adapter.is_stub is True


def test_the_stub_raises_the_exact_error(tmp_path):
    stub = HexawareExternalAgent(env_file=tmp_path / "missing.env")
    with pytest.raises(NotImplementedError) as excinfo:
        stub.call(task="MappingAgent", input={}, response_schema=Answer)
    assert str(excinfo.value) == "External agent: implement after API is known"


def test_the_stub_reads_its_keys_and_they_are_in_env_example(tmp_path, monkeypatch):
    for key in HexawareExternalAgent.ENV_KEYS:
        monkeypatch.setenv(key, f"value-of-{key}")
    stub = HexawareExternalAgent(env_file=tmp_path / "missing.env")
    assert stub.config == {key: f"value-of-{key}" for key in HexawareExternalAgent.ENV_KEYS}
    example = set(read_env(env_file=REPO_ROOT / ".env.example", include_process_env=False))
    assert {"EXTERNAL_AGENT_PROVIDER", "EXTERNAL_AGENT_FOR", *HexawareExternalAgent.ENV_KEYS} <= example


def test_validated_accepts_a_dict_and_an_instance_and_refuses_a_bad_output():
    assert validated(Answer, {"text": "x"}) == Answer(text="x")
    assert validated(Answer, Answer(text="y")) == Answer(text="y")
    for bad in ({"text": 3}, {}, "nonsense", None):
        with pytest.raises(ExternalAgentUnavailable) as excinfo:
            validated(Answer, bad)
        assert excinfo.value.reason == "malformed_output"


def test_health_reports_the_stub_only_when_it_is_selected():
    from apps.api.routers.health import unavailable_adapters

    assert "external_agents" not in unavailable_adapters(_settings())
    assert "external_agents" in unavailable_adapters(_settings(external_agent_provider="hexaware"))


def test_the_stub_imports_no_http_client_or_sdk():
    source = (REPO_ROOT / "ports" / "external_agents" / "hexaware.py").read_text(encoding="utf-8")
    roots = {a.name.split(".")[0] for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Import) for a in n.names}
    roots |= {n.module.split(".")[0] for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom) and n.module}
    assert not roots & {"requests", "httpx", "urllib", "aiohttp", "http", "socket", "openai", "azure"}
