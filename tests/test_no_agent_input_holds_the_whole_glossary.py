"""No agent is sent the whole glossary (phase 5 plan, done-when 5 and assumption 6).

A 200-element glossary: the 33 seed elements and 167 synthetic ones that all look like the search's
words. Each agent's input holds at most 10 candidate elements per column or term. Tasks 6 and 8 add the
glossary builder's and the mapping agent's parts.
"""

from __future__ import annotations

import json

import pytest

from agents.discovery_assistant import DiscoveryAssistant
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.step_chains import assist

RILEY = dict(user_id="dev-requester", roles=["requester"], display_name=lambda user_id: user_id)
EXTRA = 167


@pytest.fixture
def big_glossary(db_session, glossary):
    for n in range(EXTRA):
        glossary.add_seed_element(key=f"closing_prices_x{n:03d}", name=f"Closing prices x{n:03d}",
                                  domain="pricing", data_type="decimal",
                                  definition="A synthetic element that looks like the search.", aliases=[])
    assert len(glossary.list_current()) == 200
    return glossary


def stored_input(blob_root, agent_name):
    """The input the model was sent, as Agent.run() stored it (agent-inputs/<agent>/<id>.json)."""
    (path,) = list((blob_root / "agent-inputs" / agent_name).glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))["input"]


def test_the_discovery_assistant_gets_at_most_ten_candidates_per_term(db_session, big_glossary, tmp_path):
    agent = DiscoveryAssistant(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))
    result = assist(db_session, query="closing prices", agent=agent, **RILEY)
    sent = stored_input(tmp_path / "blob", "DiscoveryAssistant")
    keys = [g["key"] for g in sent["glossary"]]
    assert sent["unmatched_terms"] == ["closing prices"]
    assert len(keys) <= 10  # one list for the term, scored on it and its singular form
    assert len(set(keys)) == len(keys)
    assert "close_price" in keys
    assert [(s.term, s.element_key) for s in result.suggestions] == [("closing prices", "close_price")]
