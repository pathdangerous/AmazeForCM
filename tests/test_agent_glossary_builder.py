"""GlossaryBuilderAgent (step 05, A2, approver: glossary owner; phase 5 plan, assumptions 13 to 16).

Code names the outcome (candidates.classify_element), not the agent. One pending glossary_element
proposal per column that gets an answer, linked to the feed; a column with no answer gets none.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from agents.glossary_builder import GlossaryBuilderAgent
from core.domain.glossary import GlossaryElementDraft
from core.domain.mapping import GlossaryBuilderInput, GlossaryBuilderOutput, GlossaryProposalPayload, NewColumn
from core.steps.candidates import candidate_list
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.step_chains import end_read

SEED = Path(__file__).resolve().parents[1] / "fixtures" / "glossary" / "enterprise_glossary.json"
GLOSSARY = [GlossaryElementDraft(**{k: e[k] for k in ("key", "name", "domain", "data_type", "definition", "aliases")})
            for e in json.loads(SEED.read_text(encoding="utf-8"))["elements"]]
RUN = uuid.uuid4()


def column(name, type_):
    return NewColumn(name=name, type=type_, null_rate=0.0, distinct_count=10,
                     candidates=candidate_list(name, type_, GLOSSARY))


def builder_input(feed_id, *columns):
    return GlossaryBuilderInput(run_id=RUN, feed_id=feed_id, source_code="syn_bond_terms", source_name="Bond terms",
                                source_domain="security_master", domains=["pricing", "security_master"],
                                columns=list(columns))


def output(*answers):
    return GlossaryBuilderOutput(answers=[{"reason": "Synthetic reason.", **a} for a in answers])


NEW_RATING = {"key": "rating", "name": "Rating", "domain": "security_master", "data_type": "string",
              "definition": "Rating: column rating of Bond terms. To be confirmed by the glossary owner."}


def test_the_agents_declaration():
    assert (GlossaryBuilderAgent.name, GlossaryBuilderAgent.prompt_id, GlossaryBuilderAgent.autonomy,
            GlossaryBuilderAgent.approver_roles) == ("GlossaryBuilderAgent", "glossary_builder", "A2",
                                                     ("glossary_owner",))


def test_each_answered_column_is_one_proposal_with_the_outcome_code_named():
    feed = uuid.uuid4()
    agent_input = builder_input(feed, column("rating", "VARCHAR"), column("venue_mic_code", "VARCHAR"),
                                column("date", "DATE"))
    drafts = GlossaryBuilderAgent.to_proposals(None, agent_input, output(
        {"column": "rating", "new_element": NEW_RATING},
        {"column": "venue_mic_code", "same_as": "exchange_mic"},
        {"column": "date"}))  # nothing answered: left to step 06
    payloads = [GlossaryProposalPayload(**d.payload) for d in drafts]
    assert [(d.kind, d.feed_id) for d in drafts] == [("glossary_element", feed)] * 2
    assert [(p.source_field, p.outcome, p.recommended, p.run_id) for p in payloads] == [
        ("rating", "proposed", 0, RUN), ("venue_mic_code", "duplicate_alias", 0, RUN)]
    assert payloads[1].options[0].model_dump(exclude_none=True) == {"kind": "alias", "key": "exchange_mic",
                                                                   "alias": "venue_mic_code"}


def test_a_conflict_recommends_nothing_and_may_offer_nothing():
    agent_input = builder_input(uuid.uuid4(), column("maturity", "VARCHAR"))
    new = {**NEW_RATING, "key": "maturity", "name": "Maturity"}
    [draft] = GlossaryBuilderAgent.to_proposals(None, agent_input, output({"column": "maturity", "new_element": new}))
    payload = GlossaryProposalPayload(**draft.payload)
    assert (payload.outcome, payload.options, payload.recommended) == ("conflict_to_owner", [], None)


def test_answers_for_unknown_columns_or_non_candidates_or_twice_are_dropped():
    agent_input = builder_input(uuid.uuid4(), column("rating", "VARCHAR"))
    drafts = GlossaryBuilderAgent.to_proposals(None, agent_input, output(
        {"column": "not_in_input", "new_element": NEW_RATING},
        {"column": "rating", "same_as": "isin"},  # not a candidate of rating: dropped, nothing left
        {"column": "rating", "new_element": NEW_RATING}))  # the column's second answer is ignored
    assert drafts == []


def test_an_answer_is_one_of_same_as_new_element_or_nothing():
    with pytest.raises(ValueError):
        output({"column": "rating", "same_as": "isin", "new_element": NEW_RATING})


def test_a_column_carries_at_most_ten_candidates():
    with pytest.raises(ValueError):
        NewColumn(name="price", type="DOUBLE", candidates=candidate_list("price", "DOUBLE", GLOSSARY) * 2)


def test_through_agent_run_with_the_mock(db_session, tmp_path):
    from core.repositories.feeds import FeedRepository

    feed = FeedRepository(db_session).create_candidate(code="syn_bond_terms", name="Bond terms")
    agent = GlossaryBuilderAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))
    end_read(db_session)
    run = agent.run(db_session, builder_input(feed.id, column("rating", "VARCHAR"), column("venue_mic_code", "VARCHAR"),
                                              column("date", "DATE")))
    assert [(p.kind, p.payload["source_field"], p.payload["outcome"], p.required_approver_roles, p.status)
            for p in run.proposals] == [("glossary_element", "rating", "proposed", ["glossary_owner"], "pending"),
                                        ("glossary_element", "venue_mic_code", "duplicate_alias", ["glossary_owner"],
                                         "pending")]
    rating = run.proposals[0].payload["options"][0]["element"]
    assert rating == {**NEW_RATING, "aliases": [], "unit": None}  # old: without "unit" (0025)


# ---------------------------------------------------------------- phase 7.8, task 6.1: the security master (D8)

DEMO = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic" / "Synthetic_Security_Master.xlsx"


def d8_input(feed_id, tmp_path):
    from core.intake import canonical_csv, read_xlsx_bytes
    from core.steps.profiling import profile_csv

    table, _ = read_xlsx_bytes(DEMO.read_bytes(), original_name=DEMO.name)
    csv = tmp_path / "master.csv"
    csv.write_bytes(canonical_csv(table))
    return builder_input(feed_id, *[column(c.name, c.type) for c in profile_csv(csv).columns])


def test_the_d8_run_gives_every_one_of_the_thirty_columns_a_proposal(db_session, tmp_path):
    from core.repositories.feeds import FeedRepository

    feed = FeedRepository(db_session).create_candidate(code="syn_security_master", name="Security master")
    agent = GlossaryBuilderAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))
    agent_input = d8_input(feed.id, tmp_path)
    assert len(agent_input.columns) == 30 and all(len(c.candidates) <= 10 for c in agent_input.columns)
    end_read(db_session)
    run = agent.run(db_session, agent_input)
    payloads = {p.payload["source_field"]: p.payload for p in run.proposals}
    assert list(payloads) == [c.name for c in agent_input.columns]  # all thirty, in file order
    aliases = {c: (p["options"][0]["key"], p["options"][0]["alias"]) for c, p in payloads.items()
               if p["outcome"] == "duplicate_alias"}
    assert aliases == {"TICKER_NAME": ("ticker", "ticker_name"), "COUNTRY": ("country_of_risk", "country")}
    new = {c: p["options"][0]["element"] for c, p in payloads.items() if p["outcome"] == "proposed"}
    assert len(new) == 28
    assert {e["domain"] for c, e in new.items() if c.startswith("BEST_")} == {"estimates"}
    assert len([c for c in new if c.startswith("BEST_")]) == 22
    assert len({e["key"] for e in new.values()}) == len({e["name"] for e in new.values()}) == 28
    assert all(e["name"].strip() and e["definition"].strip() and e["unit"] is None for e in new.values())  # no unit guessed
    assert {e["data_type"] for c, e in new.items() if c.startswith("BEST_")} == {"decimal"}
    assert new["RECORD_DATE"]["data_type"] == "date" and new["SECTOR"]["data_type"] == "string"
