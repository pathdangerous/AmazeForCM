"""MappingAgent (step 06, A2, approver: data steward) and MappingEvaluator (read only, the same prompt);
phase 5 plan, assumptions 21 to 23, 29 and 37.

Code keeps only keys that were candidates, each once, at most 10. None left: no proposal
(no_element_back_to_05). One: a pending proposal recommending it. Several: a tie, no recommendation. A
proposal that would change nothing (its only key is the column's current element) is not created.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from agents.base import Agent
from agents.mapping import MappingAgent, MappingEvaluator
from core.domain.glossary import GlossaryElementDraft
from core.domain.mapping import ColumnToMap, MappingInput, MappingOutput, MappingProposalPayload
from core.steps.candidates import candidate_list, kept_elements
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.step_chains import end_read

SEED = Path(__file__).resolve().parents[1] / "fixtures" / "glossary" / "enterprise_glossary.json"
GLOSSARY = [GlossaryElementDraft(**{k: e[k] for k in ("key", "name", "domain", "data_type", "definition", "aliases")})
            for e in json.loads(SEED.read_text(encoding="utf-8"))["elements"]]


def column(name, type_, *, hint=None, current=None):
    return ColumnToMap(name=name, type=type_, null_rate=0.0, distinct_count=10,
                       candidates=candidate_list(name, type_, GLOSSARY, hint=hint), current=current)


def mapping_input(feed_id, *columns):
    return MappingInput(feed_id=feed_id, source_code="syn_bond_terms", source_name="Bond terms",
                        source_description=None, columns=list(columns))


def output(*answers):
    return MappingOutput(answers=[{"reason": "Synthetic reason.", **a} for a in answers])


def test_the_two_declarations():
    assert (MappingAgent.name, MappingAgent.prompt_id, MappingAgent.autonomy, MappingAgent.approver_roles) == (
        "MappingAgent", "mapping", "A2", ("data_steward",))
    assert (MappingEvaluator.name, MappingEvaluator.prompt_id, MappingEvaluator.approver_roles,
            MappingEvaluator.input_schema, MappingEvaluator.output_schema) == (
        "MappingEvaluator", "mapping", (), MappingInput, MappingOutput)
    assert MappingEvaluator.to_proposals is Agent.to_proposals  # read only


def test_one_recommends_several_are_a_tie_none_is_no_proposal():
    feed = uuid.uuid4()
    agent_input = mapping_input(feed, column("cpn", "DOUBLE"), column("date", "DATE"), column("rating", "VARCHAR"))
    drafts = MappingAgent.to_proposals(None, agent_input, output(
        {"column": "cpn", "elements": []},
        {"column": "date", "elements": ["issue_date", "maturity_date", "price_date"]},
        {"column": "rating", "elements": []}))
    [tie] = drafts
    assert tie.kind == "mapping"
    payload = MappingProposalPayload(**tie.payload)
    assert (tie.feed_id, payload.source_field, payload.candidates, payload.recommended) == (
        feed, "date", ["issue_date", "maturity_date", "price_date"], None)
    [one] = MappingAgent.to_proposals(None, mapping_input(feed, column("coupon", "DOUBLE")),
                                      output({"column": "coupon", "elements": ["coupon_rate"]}))
    assert (one.payload["candidates"], one.payload["recommended"], one.payload["column_type"]) == (
        ["coupon_rate"], "coupon_rate", "DOUBLE")


def test_code_keeps_only_candidates_each_once():
    date = column("date", "DATE")
    assert kept_elements(date, ["isin", "issue_date", "issue_date", "maturity_date"]) == ["issue_date", "maturity_date"]
    assert kept_elements(date, ["invented_element"]) == []


def test_a_proposal_that_changes_nothing_is_not_created():
    feed = uuid.uuid4()
    same = MappingAgent.to_proposals(None, mapping_input(feed, column("coupon", "DOUBLE", current="coupon_rate")),
                                     output({"column": "coupon", "elements": ["coupon_rate"]}))
    assert same == []


def test_answers_for_unknown_columns_or_twice_are_dropped():
    feed = uuid.uuid4()
    drafts = MappingAgent.to_proposals(None, mapping_input(feed, column("coupon", "DOUBLE")), output(
        {"column": "elsewhere", "elements": ["coupon_rate"]},
        {"column": "coupon", "elements": ["coupon_rate"]},
        {"column": "coupon", "elements": ["coupon_rate", "accrued_interest"]}))
    assert [d.payload["candidates"] for d in drafts] == [["coupon_rate"]]


def test_a_column_carries_at_most_ten_candidates():
    with pytest.raises(ValueError):
        ColumnToMap(name="price", type="DOUBLE", candidates=candidate_list("price", "DOUBLE", GLOSSARY) * 2)


def test_through_agent_run_with_the_mock(db_session, tmp_path):
    from core.repositories.feeds import FeedRepository

    feed = FeedRepository(db_session).create_candidate(code="syn_bond_terms", name="Bond terms")
    agent = MappingAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))
    end_read(db_session)
    run = agent.run(db_session, mapping_input(feed.id, column("instr_id", "VARCHAR", hint="instrument_id"),
                                              column("date", "DATE"), column("maturity", "VARCHAR")))
    assert [(p.payload["source_field"], p.payload["candidates"], p.payload["recommended"], p.required_approver_roles)
            for p in run.proposals] == [
        ("instr_id", ["instrument_id"], "instrument_id", ["data_steward"]),
        ("date", ["issue_date", "maturity_date", "price_date"], None, ["data_steward"])]


def test_the_log_row_names_mappingagent(db_session, tmp_path):
    """Note 6 (assumption 21): the agent's name is written to the append-only proposal_log."""
    from core.repositories.feeds import FeedRepository
    from core.repositories.tables import ProposalLogEntry

    feed = FeedRepository(db_session).create_candidate(code="syn_bond_terms", name="Bond terms")
    end_read(db_session)
    run = MappingAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob")).run(
        db_session, mapping_input(feed.id, column("coupon", "DOUBLE")))
    entry = db_session.get(ProposalLogEntry, run.log_id)
    assert (entry.agent_name, entry.prompt_id, entry.prompt_version) == ("MappingAgent", "mapping", "v2")
    assert run.proposals[0].proposal_log_id == entry.id


def test_the_evaluator_logs_its_call_and_creates_no_proposal(db_session, tmp_path):
    agent = MappingEvaluator(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))
    end_read(db_session)
    run = agent.run(db_session, mapping_input(None, column("coupon", "DOUBLE")))
    assert run.proposals == [] and run.log_id is not None
    assert [(a.column, a.elements) for a in run.output.answers] == [("coupon", ["coupon_rate"])]
