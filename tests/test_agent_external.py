"""Phase 7.18, T2: Agent.run() may route one named agent through the ExternalAgent port (EXTERNAL_AGENT_FOR).
A fake adapter only: no network. The call is logged like any other (provider "hexaware", one call row), a failure is the
typed ExternalAgentUnavailable and writes nothing, and with EXTERNAL_AGENT_FOR empty nothing changes."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from agents.base import Agent
from core.repositories.tables import ProposalLogEntry, ProposalRow
from ports.external_agents import ExternalAgentResult, ExternalAgentUnavailable
from tests.test_agent_base import (EchoAgent, EchoInput, EchoOutput, FakeProvider, _count, _discards, _end_read,  # noqa: F401
                                   blobs, prompts)

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeExternal:
    name = "hexaware"

    def __init__(self, output="default", error=None):
        self.output = {"items": ["x", "y"]} if output == "default" else output
        self.error, self.calls = error, []

    def call(self, *, task, input, response_schema):
        self.calls.append({"task": task, "input": input, "response_schema": response_schema})
        if self.error is not None:
            raise self.error
        return ExternalAgentResult.model_construct(output=self.output, provider="hexaware", model="data-agent",
                                                   model_version="2", latency_ms=9, raw_ref=None)


def _agent(blobs, prompts, *, external, provider=None, external_for="EchoAgent"):
    return EchoAgent(provider=provider or FakeProvider(), blob_store=blobs, prompts=prompts, external=external,
                     external_for=external_for)


def _counts(db_session):
    return _count(db_session, ProposalLogEntry), _count(db_session, ProposalRow)


def test_a_routed_agent_is_answered_by_the_external_agent_not_the_model(db_session, prompts, blobs):
    external, provider = FakeExternal(), FakeProvider()
    run = _agent(blobs, prompts, external=external, provider=provider).run(db_session, EchoInput(text="hi"))
    assert provider.calls == []
    assert external.calls == [{"task": "EchoAgent", "input": {"text": "hi"}, "response_schema": EchoOutput}]
    assert run.output == EchoOutput(items=["x", "y"])
    assert [p.payload for p in run.proposals] == [{"item": "x"}, {"item": "y"}]


def test_the_routed_call_is_logged_with_provider_hexaware_and_one_call_row(db_session, prompts, blobs):
    before = _counts(db_session)
    _end_read(db_session)
    run = _agent(blobs, prompts, external=FakeExternal()).run(db_session, EchoInput(text="hi"))
    assert _counts(db_session) == (before[0] + 1, before[1] + 2)
    entry = db_session.get(ProposalLogEntry, run.log_id)
    assert (entry.agent_name, entry.provider, entry.model, entry.model_version) == ("EchoAgent", "hexaware",
                                                                                     "data-agent", "2")
    assert (entry.prompt_id, entry.prompt_version, entry.latency_ms) == ("external:EchoAgent", "external", 9)
    assert entry.output == {"items": ["x", "y"]} and entry.input_ref == run.input_ref
    assert all(p.proposal_log_id == run.log_id for p in db_session.scalars(select(ProposalRow)).all()[-2:])
    stored = json.loads(blobs.get(run.input_ref))
    assert stored["input"] == {"text": "hi"} and stored["external"] is True and stored["messages"] == []


def test_a_routed_call_keeps_the_before_write_pattern(db_session, prompts, blobs):
    before = _counts(db_session)
    _end_read(db_session)

    def refuse(session):
        raise LookupError("moved on during the call")

    with pytest.raises(LookupError):
        _agent(blobs, prompts, external=FakeExternal()).run(db_session, EchoInput(text="x"), before_write=refuse)
    assert _counts(db_session) == (before[0] + 1, before[1])  # one call row, no proposal
    [(log_id, reason)] = _discards(db_session)
    assert reason.startswith("LookupError") and db_session.get(ProposalLogEntry, log_id).provider == "hexaware"


def test_a_routed_call_refuses_a_session_in_a_transaction(db_session, prompts, blobs):
    from agents.base import SessionInTransaction

    db_session.execute(select(ProposalRow).limit(1))  # opens a transaction
    external = FakeExternal()
    with pytest.raises(SessionInTransaction):
        _agent(blobs, prompts, external=external).run(db_session, EchoInput(text="x"))
    assert external.calls == []


@pytest.mark.parametrize("external_for", ["", "SomeOtherAgent"])
def test_not_named_means_the_model_answers_as_today(db_session, prompts, blobs, external_for):
    external, provider = FakeExternal(), FakeProvider()
    run = _agent(blobs, prompts, external=external, provider=provider, external_for=external_for).run(
        db_session, EchoInput(text="x"))
    assert external.calls == [] and len(provider.calls) == 1
    entry = db_session.get(ProposalLogEntry, run.log_id)
    assert (entry.provider, entry.prompt_id) == ("fake", "echo")


def test_with_no_adapter_nothing_is_routed_even_if_a_name_is_set(db_session, prompts, blobs):
    provider = FakeProvider()  # EXTERNAL_AGENT_PROVIDER=none: get_external_agent() gives no adapter
    EchoAgent(provider=provider, blob_store=blobs, prompts=prompts, external_for="EchoAgent").run(
        db_session, EchoInput(text="x"))
    assert len(provider.calls) == 1


def test_by_default_the_settings_decide_and_the_defaults_route_nothing(db_session, prompts, blobs):
    provider = FakeProvider()
    EchoAgent(provider=provider, blob_store=blobs, prompts=prompts).run(db_session, EchoInput(text="x"))
    assert len(provider.calls) == 1


def test_the_settings_route_to_the_stub_and_the_stub_gives_the_typed_outcome(db_session, prompts, blobs, monkeypatch):
    from ports.config import get_settings

    monkeypatch.setenv("EXTERNAL_AGENT_PROVIDER", "hexaware")
    monkeypatch.setenv("EXTERNAL_AGENT_FOR", "EchoAgent")
    get_settings.cache_clear()
    try:
        before, provider = _counts(db_session), FakeProvider()
        _end_read(db_session)
        with pytest.raises(ExternalAgentUnavailable) as excinfo:
            EchoAgent(provider=provider, blob_store=blobs, prompts=prompts).run(db_session, EchoInput(text="x"))
        assert excinfo.value.reason == "not_implemented" and provider.calls == []
        assert _counts(db_session) == before and blobs.list_keys() == []
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(("error", "reason"), [
    (ExternalAgentUnavailable("timeout", "slow"), "timeout"),
    (NotImplementedError("External agent: implement after API is known"), "not_implemented"),
    (RuntimeError("boom"), "provider_error"),
])
def test_a_failed_external_call_is_the_typed_outcome_and_writes_nothing(db_session, prompts, blobs, error, reason):
    before = _counts(db_session)
    _end_read(db_session)
    with pytest.raises(ExternalAgentUnavailable) as excinfo:
        _agent(blobs, prompts, external=FakeExternal(error=error)).run(db_session, EchoInput(text="x"))
    assert excinfo.value.reason == reason
    assert _counts(db_session) == before and blobs.list_keys() == [] and _discards(db_session) == []


@pytest.mark.parametrize("bad", [{"items": "not a list"}, {"wrong": 1}, "text", None])
def test_an_output_that_fails_the_schema_is_refused_and_writes_nothing(db_session, prompts, blobs, bad):
    before = _counts(db_session)
    _end_read(db_session)
    with pytest.raises(ExternalAgentUnavailable) as excinfo:
        _agent(blobs, prompts, external=FakeExternal(output=bad)).run(db_session, EchoInput(text="x"))
    assert excinfo.value.reason == "malformed_output"
    assert _counts(db_session) == before and blobs.list_keys() == []


def test_the_api_answers_503_with_the_typed_outcome():
    import asyncio

    from apps.api.main import app

    handler = app.exception_handlers[ExternalAgentUnavailable]
    response = asyncio.run(handler(None, ExternalAgentUnavailable("timeout", "slow")))
    assert response.status_code == 503
    assert json.loads(response.body) == {"outcome": "external_agent_unavailable", "reason": "timeout",
                                         "detail": "The external agent did not answer. Try again later."}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    return found | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}


def test_only_agents_base_uses_the_port_and_core_never_does():
    users = {}
    for top in ("core", "agents", "apps", "ports"):
        for path in (REPO_ROOT / top).rglob("*.py"):
            if top == "ports" and path.parts[-2] == "external_agents":
                continue
            if any(m == "ports.external_agents" or m.startswith("ports.external_agents.") for m in _imports(path)):
                users[path.relative_to(REPO_ROOT).as_posix()] = path
    # health.py reads the selected adapter's flags only (phase 7.18 plan, assumption 8); it never calls it
    assert sorted(users) == ["agents/base.py", "apps/api/main.py", "apps/api/routers/health.py"]
    for name in ("apps/api/main.py", "apps/api/routers/health.py"):
        assert ".call(" not in users[name].read_text(encoding="utf-8")
    assert not [p for p in users if p.startswith("core/")]
