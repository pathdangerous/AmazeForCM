"""agents/base.Agent (phase 3 plan, assumptions 19 and 20; carried item 1).

Agent.run() is the only path to the model. It renders the prompt, calls the provider, stores the input,
logs the call in proposal_log and only then creates the proposals, each pointing at that log row. A
subclass that defines run() is refused when the class is created.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from agents.base import Agent, AgentProposal, ReadOnlyAgentProposed
from agents.prompt_loader import PromptLibrary
from core.domain.sourcing import AgentRun
from core.repositories.tables import ProposalLogEntry, ProposalRow
from ports.llm.base import LLMResult
from ports.storage.local import LocalBlobStore

ECHO_PROMPT = """\
prompt_id: echo
version: v1
system: |
  You echo items.
user: |
  Echo the items below.

  {input_json}
"""


class EchoInput(BaseModel):
    text: str


class EchoOutput(BaseModel):
    items: list[str]


class EchoAgent(Agent):
    name = "EchoAgent"
    prompt_id = "echo"
    autonomy = "A2"
    approver_role = "requester"
    input_schema = EchoInput
    output_schema = EchoOutput

    def to_proposals(self, agent_input, output):
        return [AgentProposal(kind="scope", payload={"item": item}) for item in output.items]


class LookOnlyAgent(Agent):
    name = "LookOnlyAgent"
    prompt_id = "echo"
    autonomy = "A2"
    approver_role = None
    input_schema = EchoInput
    output_schema = EchoOutput


class FakeProvider:
    """Stands in for an LLMProvider: answers with a fixed output, or raises."""

    name = "fake"

    def __init__(self, output=None, error=None, events=None):
        self.output, self.error, self.calls = output or {"items": ["a", "b"]}, error, []
        self.events = events if events is not None else []

    def complete(self, *, prompt_id, prompt_version, messages, response_schema, temperature=0.0):
        self.calls.append({"prompt_id": prompt_id, "prompt_version": prompt_version, "messages": messages})
        self.events.append("complete")
        if self.error is not None:
            raise self.error
        return LLMResult(output=response_schema.model_validate(self.output), provider="fake", model="fake-model",
                         model_version="7", prompt_id=prompt_id, prompt_version=prompt_version, latency_ms=3,
                         raw_ref=None)


@pytest.fixture
def prompts(tmp_path):
    root = tmp_path / "prompts"
    root.mkdir()
    (root / "echo.v1.yaml").write_text(ECHO_PROMPT, encoding="utf-8")
    return PromptLibrary(root, {"echo": "v1"})


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blob")


def _count(session, table):
    return session.scalar(select(func.count()).select_from(table))


# ---------------------------------------------------------------- the class itself


def test_a_subclass_that_defines_run_is_refused_at_class_creation():
    with pytest.raises(TypeError, match="run"):
        class Sneaky(EchoAgent):  # noqa: F841 (the class statement is the test)
            def run(self, session, agent_input):
                return None


def test_a_subclass_must_declare_every_attribute():
    with pytest.raises(TypeError, match="prompt_id"):
        class Incomplete(Agent):  # noqa: F841
            name = "Incomplete"
            autonomy = "A2"
            approver_role = "requester"
            input_schema = EchoInput
            output_schema = EchoOutput


@pytest.mark.parametrize("field, value", [("autonomy", "A3"), ("approver_role", "nobody")])
def test_autonomy_and_approver_role_are_checked(field, value):
    attributes = {"name": "Bad", "prompt_id": "echo", "autonomy": "A2", "approver_role": "requester",
                  "input_schema": EchoInput, "output_schema": EchoOutput, field: value}
    with pytest.raises(TypeError, match=field):
        type("Bad", (Agent,), attributes)


def test_a_read_only_agent_cannot_define_to_proposals():
    with pytest.raises(TypeError, match="read-only"):
        class ReadOnlyWithDrafts(LookOnlyAgent):  # noqa: F841
            def to_proposals(self, agent_input, output):
                return []


# ---------------------------------------------------------------- run()


def test_run_logs_the_call_before_it_creates_proposals(db_session, prompts, blobs, monkeypatch):
    events = []
    import agents.base as base

    original_record, original_create = base.ProposalLog.record, base.ProposalRepository.create

    def record(self, **kwargs):
        events.append("log")
        return original_record(self, **kwargs)

    def create(self, **kwargs):
        events.append("proposal")
        return original_create(self, **kwargs)

    monkeypatch.setattr(base.ProposalLog, "record", record)
    monkeypatch.setattr(base.ProposalRepository, "create", create)
    EchoAgent(provider=FakeProvider(events=events), blob_store=blobs, prompts=prompts).run(
        db_session, EchoInput(text="hello"))
    assert events == ["complete", "log", "proposal", "proposal"]


def test_every_proposal_points_at_the_log_row(db_session, prompts, blobs):
    run = EchoAgent(provider=FakeProvider(), blob_store=blobs, prompts=prompts).run(db_session, EchoInput(text="x"))
    assert isinstance(run, AgentRun)
    assert len(run.proposals) == 2
    assert {p.proposal_log_id for p in run.proposals} == {run.log_id}
    stored = db_session.scalars(select(ProposalRow).where(ProposalRow.proposal_log_id == run.log_id)).all()
    assert sorted(row.payload["item"] for row in stored) == ["a", "b"]


def test_the_agent_declares_autonomy_and_approver_on_every_proposal(db_session, prompts, blobs):
    run = EchoAgent(provider=FakeProvider(), blob_store=blobs, prompts=prompts).run(db_session, EchoInput(text="x"))
    assert {(p.autonomy, tuple(p.required_approver_roles), p.status) for p in run.proposals} == {
        ("A2", ("requester",), "pending")
    }


def test_the_log_row_holds_every_rule_4_field(db_session, prompts, blobs):
    run = EchoAgent(provider=FakeProvider(), blob_store=blobs, prompts=prompts).run(db_session, EchoInput(text="x"))
    entry = db_session.get(ProposalLogEntry, run.log_id)
    assert (entry.agent_name, entry.provider, entry.model, entry.model_version) == ("EchoAgent", "fake",
                                                                                     "fake-model", "7")
    assert (entry.prompt_id, entry.prompt_version, entry.latency_ms) == ("echo", "v1", 3)
    assert entry.output == {"items": ["a", "b"]}
    assert entry.input_ref == run.input_ref


def test_the_input_reference_is_a_stored_copy_of_the_input_and_messages(db_session, prompts, blobs):
    provider = FakeProvider()
    run = EchoAgent(provider=provider, blob_store=blobs, prompts=prompts).run(db_session, EchoInput(text="hello"))
    assert run.input_ref.startswith("agent-inputs/EchoAgent/") and run.input_ref.endswith(".json")
    stored = json.loads(blobs.get(run.input_ref))
    assert stored["input"] == {"text": "hello"}
    assert stored["prompt_id"] == "echo" and stored["prompt_version"] == "v1"
    assert stored["messages"] == [m.model_dump() for m in provider.calls[0]["messages"]]


def test_the_user_message_ends_with_the_input_as_json(db_session, prompts, blobs):
    provider = FakeProvider()
    EchoAgent(provider=provider, blob_store=blobs, prompts=prompts).run(db_session, EchoInput(text="hello"))
    system, user = provider.calls[0]["messages"]
    assert (system.role, user.role) == ("system", "user")
    assert system.content.strip() == "You echo items."
    assert user.content.rstrip().endswith('```json\n{"text": "hello"}\n```')


def test_a_failed_model_call_writes_nothing(db_session, prompts, blobs):
    before = (_count(db_session, ProposalLogEntry), _count(db_session, ProposalRow))
    agent = EchoAgent(provider=FakeProvider(error=RuntimeError("model unavailable")), blob_store=blobs,
                      prompts=prompts)
    with pytest.raises(RuntimeError, match="model unavailable"):
        agent.run(db_session, EchoInput(text="x"))
    assert (_count(db_session, ProposalLogEntry), _count(db_session, ProposalRow)) == before
    assert blobs.list_keys() == []


def test_a_read_only_agent_cannot_create_proposals(db_session, prompts, blobs, monkeypatch):
    agent = LookOnlyAgent(provider=FakeProvider(), blob_store=blobs, prompts=prompts)
    run = agent.run(db_session, EchoInput(text="x"))
    assert run.proposals == []
    assert db_session.get(ProposalLogEntry, run.log_id) is not None  # rule 4: logged even though read only

    # A drafts method attached after class creation is still refused at run time.
    monkeypatch.setattr(LookOnlyAgent, "to_proposals",
                        lambda self, i, o: [AgentProposal(kind="scope", payload={})])
    before = _count(db_session, ProposalRow)
    with pytest.raises(ReadOnlyAgentProposed):
        agent.run(db_session, EchoInput(text="x"))
    assert _count(db_session, ProposalRow) == before


def test_run_refuses_an_input_of_the_wrong_schema(db_session, prompts, blobs):
    class Other(BaseModel):
        text: str

    with pytest.raises(TypeError, match="EchoInput"):
        EchoAgent(provider=FakeProvider(), blob_store=blobs, prompts=prompts).run(db_session, Other(text="x"))


def test_run_flushes_but_never_commits(db_session, prompts, blobs, monkeypatch):
    def no_commit():
        raise AssertionError("Agent.run() must not commit")

    monkeypatch.setattr(db_session, "commit", no_commit)
    run = EchoAgent(provider=FakeProvider(), blob_store=blobs, prompts=prompts).run(db_session, EchoInput(text="x"))
    assert db_session.get(ProposalLogEntry, run.log_id) is not None
