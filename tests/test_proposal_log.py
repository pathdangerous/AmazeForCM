"""ProposalLog (CLAUDE.md rule 4): every AI proposal logged with agent, provider, model and version,
prompt version, input reference and output; then who approved or rejected it, once."""

import uuid

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from core.proposal_log import ProposalAlreadyDecided, ProposalLog, ProposalLogEntry, ProposalNotFound
from ports.llm.base import Message
from ports.llm.mock import MockProvider


class SmokeOutput(BaseModel):
    summary: str
    confidence: float


@pytest.fixture
def result():
    return MockProvider().complete(
        prompt_id="phase0_smoke", prompt_version="v1",
        messages=[Message(role="user", content="hi")], response_schema=SmokeOutput,
    )


def test_record_stores_every_rule_4_field(db_session, result):
    entry = ProposalLog(db_session).record(agent_name="SmokeAgent", input_ref="blob://inputs/1", result=result)
    db_session.expire_all()
    stored = db_session.get(ProposalLogEntry, entry.id)
    assert stored.agent_name == "SmokeAgent"
    assert stored.provider == "mock"
    assert stored.model == "mock"
    assert stored.model_version == "1"
    assert stored.prompt_id == "phase0_smoke"
    assert stored.prompt_version == "v1"
    assert stored.input_ref == "blob://inputs/1"
    assert stored.output == result.output.model_dump(mode="json")
    assert stored.latency_ms == 0
    assert stored.raw_ref is None
    assert stored.status == "pending"
    assert stored.created_at is not None
    assert (stored.decided_by, stored.decided_at, stored.decision_note) == (None, None, None)


def test_record_decision_stores_who_and_when(db_session, result):
    log = ProposalLog(db_session)
    entry = log.record(agent_name="SmokeAgent", input_ref="ref", result=result)
    decided = log.record_decision(entry.id, decision="approved", decided_by="dev-data-steward", note="looks right")
    db_session.expire_all()
    stored = db_session.get(ProposalLogEntry, decided.id)
    assert stored.status == "approved"
    assert stored.decided_by == "dev-data-steward"
    assert stored.decided_at is not None
    assert stored.decision_note == "looks right"


def test_second_decision_is_refused_and_first_kept(db_session, result):
    log = ProposalLog(db_session)
    entry = log.record(agent_name="SmokeAgent", input_ref="ref", result=result)
    log.record_decision(entry.id, decision="rejected", decided_by="dev-data-steward")
    with pytest.raises(ProposalAlreadyDecided):
        log.record_decision(entry.id, decision="approved", decided_by="dev-checker")
    db_session.expire_all()
    stored = db_session.get(ProposalLogEntry, entry.id)
    assert (stored.status, stored.decided_by) == ("rejected", "dev-data-steward")


def test_decision_on_unknown_entry(db_session):
    with pytest.raises(ProposalNotFound):
        ProposalLog(db_session).record_decision(uuid.uuid4(), decision="approved", decided_by="dev-checker")


@pytest.mark.parametrize(("decision", "who"), [("maybe", "dev-checker"), ("approved", ""), ("approved", "  ")])
def test_invalid_decision_or_empty_approver(db_session, result, decision, who):
    log = ProposalLog(db_session)
    entry = log.record(agent_name="SmokeAgent", input_ref="ref", result=result)
    with pytest.raises(ValueError):
        log.record_decision(entry.id, decision=decision, decided_by=who)


@pytest.mark.parametrize("field", ["agent_name", "input_ref"])
def test_record_requires_agent_name_and_input_ref(db_session, result, field):
    kwargs = {"agent_name": "SmokeAgent", "input_ref": "ref", field: " "}
    with pytest.raises(ValueError):
        ProposalLog(db_session).record(result=result, **kwargs)


def test_one_row_per_call(db_session, result):
    log = ProposalLog(db_session)
    log.record(agent_name="SmokeAgent", input_ref="ref", result=result)
    log.record(agent_name="SmokeAgent", input_ref="ref", result=result)
    assert len(db_session.scalars(select(ProposalLogEntry)).all()) == 2
