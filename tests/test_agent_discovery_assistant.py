"""DiscoveryAssistant (step 17, A2, read only; phase 3 plan, assumptions 38 to 40).

Suggests glossary elements for the words a search did not match. It writes nothing but its proposal_log
row (rule 4) and decides nothing. Code drops any key that is not in the glossary.
"""

from __future__ import annotations

import pytest

from agents.discovery_assistant import DiscoveryAssistant
from core.domain.sourcing import AssistantOutput
from core.repositories.tables import ProposalLogEntry, ProposalRow
from core.steps.s17_discover import assist
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.stand_in_agents import StandInAgent

RILEY = dict(user_id="dev-requester", roles=["requester"], display_name=lambda user_id: user_id)


def assistant(tmp_path):
    return DiscoveryAssistant(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))


def stand_in(suggestions):
    return StandInAgent(name="DiscoveryAssistant", output_schema=AssistantOutput,
                        output={"suggestions": suggestions})


def test_the_agents_declaration():
    assert (DiscoveryAssistant.name, DiscoveryAssistant.prompt_id, DiscoveryAssistant.autonomy,
            DiscoveryAssistant.approver_role) == ("DiscoveryAssistant", "discovery_assistant", "A2", None)


def test_closing_prices_suggests_close_price_with_the_products_that_hold_it(db_session, glossary, make_product,
                                                                           tmp_path):
    make_product("synthetic_prices", element_keys=("close_price",))
    result = assist(db_session, query="closing prices", agent=assistant(tmp_path), **RILEY)
    [suggestion] = result.suggestions
    assert (suggestion.term, suggestion.element_key, suggestion.element_name) == ("closing prices", "close_price",
                                                                                  "Close price")
    assert [(h.outcome, h.product.code) for h in suggestion.products] == [("found", "synthetic_prices")]
    entry = db_session.get(ProposalLogEntry, result.log_id)
    assert (entry.agent_name, entry.prompt_id) == ("DiscoveryAssistant", "discovery_assistant")


def test_suggestions_are_glossary_elements_only(db_session, glossary):
    agent = stand_in([{"term": "closing prices", "element_key": "close_price", "reason": "r"},
                      {"term": "closing prices", "element_key": "made_up_element", "reason": "invented"},
                      {"term": "closing prices", "element_key": "close_price", "reason": "again"},
                      {"term": "not asked", "element_key": "open_price", "reason": "a word nobody searched"}])
    result = assist(db_session, query="closing prices", agent=agent, **RILEY)
    assert [(s.term, s.element_key) for s in result.suggestions] == [("closing prices", "close_price")]


def test_the_assistant_writes_only_its_log_row(db_session, glossary, tmp_path):
    from sqlalchemy import func, select

    before = db_session.scalar(select(func.count()).select_from(ProposalRow))
    result = assist(db_session, query="closing prices", agent=assistant(tmp_path), **RILEY)
    assert result.log_id is not None
    assert db_session.scalar(select(func.count()).select_from(ProposalRow)) == before


def test_a_search_with_nothing_unmatched_calls_no_model(db_session, glossary):
    agent = stand_in([])
    result = assist(db_session, query="close price", agent=agent, **RILEY)
    assert (result.log_id, result.suggestions, agent.inputs) == (None, [], [])


def test_the_assistant_sees_the_unmatched_terms_and_the_glossary(db_session, glossary):
    agent = stand_in([])
    assist(db_session, query="close price and closing prices", agent=agent, **RILEY)
    sent = agent.inputs[0]
    assert sent.unmatched_terms == ["closing prices"]
    assert len(sent.glossary) == 33 and {"key": "close_price", "name": "Close price",
                                         "aliases": ["closing_price", "close"]} in [g.model_dump() for g in sent.glossary]


@pytest.mark.parametrize("q", ["", "   "])
def test_a_blank_query_asks_nothing(db_session, glossary, q):
    agent = stand_in([])
    assert assist(db_session, query=q, agent=agent, **RILEY).suggestions == []
    assert agent.inputs == []
