"""ProposalLog (CLAUDE.md rule 4): every model call is logged with agent, provider, model and version,
prompt id and version, input reference and output. Who approved or rejected a proposal is recorded in
the approvals table (tests/test_repo_proposals.py), not here."""

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from core.proposal_log import ProposalLog
from core.repositories.tables import ProposalLogEntry
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
    assert stored.created_at is not None


def test_log_has_no_decision_of_its_own():
    assert not hasattr(ProposalLog, "record_decision")
    assert not {"status", "decided_by", "decided_at", "decision_note"} & set(ProposalLogEntry.__table__.columns.keys())


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
