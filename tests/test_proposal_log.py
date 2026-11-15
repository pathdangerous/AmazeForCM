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


PROPOSAL = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, status) "
            "VALUES (:log, 'scope', '{}', 'A2', ARRAY['requester'], 'pending')")


def _refused(db_session, sql, params, match):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match=match):
        with db_session.begin_nested():
            db_session.execute(text(sql), params)


def test_a_discard_is_a_second_row_pointing_at_the_call(db_session, result):
    """0012: the call row stays as it was; the discard is a row of its own in proposal_log_discards."""
    from core.repositories.tables import ProposalLogDiscardRow

    log = ProposalLog(db_session)
    call = log.record(agent_name="SmokeAgent", input_ref="ref", result=result)
    discard = log.discard(proposal_log_id=call.id, reason="moved on")
    stored = db_session.get(ProposalLogDiscardRow, discard.id)
    assert (stored.proposal_log_id, stored.reason) == (call.id, "moved on")
    with pytest.raises(ValueError):
        log.discard(proposal_log_id=call.id, reason=" ")


def test_the_database_keeps_discards_and_proposals_apart(db_session, result):
    """A proposal cannot point at a discarded call; a call with a proposal cannot be discarded; a call is
    discarded once, with a reason."""
    log = ProposalLog(db_session)
    discarded = log.record(agent_name="SmokeAgent", input_ref="ref", result=result)
    log.discard(proposal_log_id=discarded.id, reason="moved on")
    _refused(db_session, PROPOSAL, {"log": discarded.id}, "was discarded")
    discard = "INSERT INTO proposal_log_discards (proposal_log_id, reason) VALUES (:log, :reason)"
    _refused(db_session, discard, {"log": discarded.id, "reason": "again"}, "uq_proposal_log_discards_proposal_log_id")

    used = log.record(agent_name="SmokeAgent", input_ref="ref", result=result)
    _refused(db_session, discard, {"log": used.id, "reason": " "}, "ck_proposal_log_discards_reason")
    from sqlalchemy import text

    db_session.execute(text(PROPOSAL), {"log": used.id})
    _refused(db_session, discard, {"log": used.id, "reason": "late"}, "has a proposal")


def test_one_row_per_call(db_session, result):
    log = ProposalLog(db_session)
    log.record(agent_name="SmokeAgent", input_ref="ref", result=result)
    log.record(agent_name="SmokeAgent", input_ref="ref", result=result)
    assert len(db_session.scalars(select(ProposalLogEntry)).all()) == 2
