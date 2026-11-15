"""IntentAgent (S1; phase 3 plan, assumption 27) on Agent.run() with the MockProvider's rule."""

from __future__ import annotations

import uuid

import pytest

from agents.intent import IntentAgent
from core.domain.sourcing import ChannelRef, ElementRef, IntentInput
from core.repositories.tables import ProposalLogEntry
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.step_chains import end_read

CHANNELS = [ChannelRef(channel="exchanges", visibility="public"),
            ChannelRef(channel="market_data_vendors", visibility="public"),
            ChannelRef(channel="internal_systems", visibility="private")]


def _input(**over):
    fields = {"requirement_id": uuid.uuid4(), "requirement_text": "open price", "purpose": "Desk analysis",
              "note": None, "elements": [ElementRef(key="open_price", name="Open price")], "terms": [],
              "channels": CHANNELS, **over}
    return IntentInput(**fields)


@pytest.fixture
def agent(tmp_path):
    return IntentAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))


def test_the_agents_declaration():
    assert (IntentAgent.name, IntentAgent.prompt_id, IntentAgent.autonomy, IntentAgent.approver_roles) == (
        "IntentAgent", "intent", "A2", ("requester",))


def test_one_scope_proposal_for_the_requester(db_session, agent, glossary):
    from core.repositories.requirements import RequirementRepository

    req = RequirementRepository(db_session).create(requester_id="dev-requester", title="open price")
    end_read(db_session)
    run = agent.run(db_session, _input(requirement_id=req.id))
    [proposal] = run.proposals
    assert (proposal.kind, proposal.required_approver_roles, proposal.autonomy, proposal.requirement_id) == (
        "scope", ["requester"], "A2", req.id)
    assert proposal.payload["visibility"] == "public"
    assert proposal.payload["where_to_search"] == ["exchanges", "market_data_vendors"]
    entry = db_session.get(ProposalLogEntry, run.log_id)
    assert (entry.agent_name, entry.prompt_id, entry.prompt_version) == ("IntentAgent", "intent", "v1")


def test_a_private_word_in_the_purpose_gives_a_private_scope(db_session, agent, glossary):
    from core.repositories.requirements import RequirementRepository

    req = RequirementRepository(db_session).create(requester_id="dev-requester", title="yield to maturity")
    end_read(db_session)
    run = agent.run(db_session, _input(requirement_id=req.id, purpose="internal desk data"))
    assert (run.proposals[0].payload["visibility"], run.proposals[0].payload["where_to_search"]) == (
        "private", ["internal_systems"])


def test_a_channel_the_agent_was_not_given_is_dropped():
    """Assumption 27: where_to_search is chosen from the input's channels only."""
    from core.domain.sourcing import IntentOutput

    output = IntentOutput(visibility="public", provider="P", where_to_search=["exchanges", "the_dark_web"],
                          reason="r")
    [draft] = IntentAgent.to_proposals(None, _input(), output)
    assert draft.payload["where_to_search"] == ["exchanges"]


def test_no_channel_left_falls_back_to_every_channel_of_the_visibility():
    from core.domain.sourcing import IntentOutput

    output = IntentOutput(visibility="private", provider="P", where_to_search=["nowhere"], reason="r")
    [draft] = IntentAgent.to_proposals(None, _input(), output)
    assert draft.payload["where_to_search"] == ["internal_systems"]
