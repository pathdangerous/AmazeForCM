"""TransformationAgent (step 10, A2, approver: data steward; phase 7.8, task 4.3, assumptions 27 to 29) on Agent.run()
with the MockProvider, on the profile of the security master (decision D8)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from agents.transformation import TransformationAgent
from core.domain.profiling import SourceSpec
from core.domain.transformation import (
    ColumnToTransform,
    ColumnTransforms,
    TransformationInput,
    TransformationOutput,
    Trim,
)
from core.intake import canonical_csv, read_xlsx_bytes
from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.tables import ProposalLogDiscardRow
from core.steps.profiling import profile_csv
from core.steps.s10_transform import transform_input
from ports.llm.base import LLMResult
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.configured import approved_element, approved_mapping, new_element
from tests.onboarded import registered_feed
from tests.step_chains import end_read

DEMO = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic" / "Synthetic_Security_Master.xlsx"
MAPPED = (("TICKER_NAME", "sec_ticker_name", "string"), ("COUNTRY", "sec_country", "string"),
          ("REGION", "sec_region", "string"), ("F_PERIOD", "sec_f_period", "string"),
          ("RECORD_DATE", "sec_record_date", "date"), ("BEST_EPS", "sec_best_eps", "decimal"))


def master_mapped(db_session, tmp_path, code="syn_security_master"):
    """The security master's real column facts (profiled from the D8 file), a registered feed with those columns,
    six of them mapped through approved proposals. Returns the feed id. Needs the `glossary` fixture."""
    table, _ = read_xlsx_bytes(DEMO.read_bytes(), original_name=DEMO.name)
    csv = tmp_path / "master.csv"
    csv.write_bytes(canonical_csv(table))
    columns = profile_csv(csv).columns
    feed = FeedRepository(db_session).create_candidate(code=code, name="Security master")
    registered_feed(db_session, feed.id, columns=columns,
                    spec=SourceSpec(source_code=code, elements=[], other_fields=[], columns={}))
    for field, key, kind in MAPPED:
        approved_element(db_session, new_element(key, data_type=kind), feed_id=feed.id, column=field)
        approved_mapping(db_session, feed.id, field, key)
    return feed.id


def agent_for(tmp_path, provider=None):
    return TransformationAgent(provider=provider or MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))


def test_the_agents_declaration():
    assert (TransformationAgent.name, TransformationAgent.prompt_id, TransformationAgent.autonomy,
            TransformationAgent.approver_roles) == ("TransformationAgent", "transformation", "A2", ("data_steward",))


def test_the_mock_nulls_the_placeholders_and_parses_the_text_date_and_leaves_clean_columns_alone(
        db_session, glossary, tmp_path):
    feed_id = master_mapped(db_session, tmp_path)
    agent_input = transform_input(db_session, feed_id=feed_id)
    end_read(db_session)
    assert {c.source_field for c in agent_input.columns} == {m[0] for m in MAPPED}  # only the mapped columns
    assert agent_input.samples == {}  # SEND_SAMPLE_VALUES is false
    run = agent_for(tmp_path).run(db_session, agent_input)
    made = {p.payload["source_field"]: p for p in run.proposals}
    assert set(made) == {"COUNTRY", "REGION", "F_PERIOD", "RECORD_DATE"}  # TICKER_NAME and BEST_EPS are clean
    assert made["COUNTRY"].payload["steps"] == [{"op": "placeholder_to_null", "values": ["ALL"]}]
    assert made["REGION"].payload["steps"] == [{"op": "placeholder_to_null", "values": ["ALL"]}]
    assert made["F_PERIOD"].payload["steps"] == [{"op": "placeholder_to_null", "values": ["NA"]}]
    assert made["RECORD_DATE"].payload["steps"] == [{"op": "parse_date", "format": "DD-MM-YYYY"}]
    date = made["RECORD_DATE"]
    assert (date.payload["element_key"], date.payload["confidence"]) == ("sec_record_date", 0.95)
    assert date.payload["reason"]
    for proposal in run.proposals:
        assert (proposal.kind, proposal.feed_id, proposal.autonomy, proposal.required_approver_roles,
                proposal.status) == ("transformation", feed_id, "A2", ["data_steward"], "pending")
        assert set(proposal.payload) == {"source_field", "element_key", "steps", "reason", "confidence"}


def test_the_prompt_limits_the_agent_to_the_fixed_set_and_tells_it_it_only_proposes():
    from agents.prompt_loader import PromptLibrary

    system = PromptLibrary().get("transformation").system
    for word in ("trim", "placeholder_to_null", "parse_date", "cast", "rename", "ambiguous", "0.6",
                 "never invent", "data steward"):
        assert word in system.lower(), word


def _column(name, **facts):
    base = dict(source_field=name, element_key=f"sec_{name.lower()}", element_type="string", duckdb_type="VARCHAR",
                null_count=0, row_count=10, placeholder_values=[], placeholder_count=0, text_date_format=None,
                date_format_ambiguous=False, all_empty=False, constant=False)
    return ColumnToTransform(**{**base, **facts})


def _input(*columns):
    return TransformationInput(feed_id=uuid.uuid4(), feed_code="syn_a", columns=list(columns))


def test_the_mock_rule_orders_the_placeholder_before_the_date_and_marks_an_ambiguous_format_for_a_person():
    from ports.llm.mock_rules import RULES

    both = _column("d", element_type="date", placeholder_values=["-"], placeholder_count=2,
                   text_date_format="DD/MM/YYYY", date_format_ambiguous=True)
    clean = _column("c")
    not_a_date = _column("s", text_date_format="YYYY-MM-DD")  # a text date, but the element is not a date
    data = _input(both, clean, not_a_date).model_dump(mode="json")
    answers = {a["source_field"]: a for a in RULES["transformation"](data)["columns"]}
    assert answers["d"]["steps"] == [{"op": "placeholder_to_null", "values": ["-"]},
                                     {"op": "parse_date", "format": "DD/MM/YYYY"}]
    assert answers["d"]["confidence"] == 0.6 and answers["d"]["reason"]
    assert (answers["c"]["steps"], answers["c"]["confidence"]) == ([], 0.95)
    assert answers["s"]["steps"] == []


def test_a_column_without_steps_or_not_in_the_input_makes_no_proposal():
    agent_input = _input(_column("COUNTRY"))
    output = TransformationOutput(columns=[
        ColumnTransforms(source_field="COUNTRY", steps=[], reason="Clean.", confidence=0.9),
        ColumnTransforms(source_field="OTHER", steps=[Trim(op="trim")], reason="Not in the input.", confidence=0.9)])
    assert TransformationAgent.to_proposals(None, agent_input, output) == []


def test_a_column_named_twice_makes_one_proposal():
    agent_input = _input(_column("COUNTRY"))
    one = ColumnTransforms(source_field="COUNTRY", steps=[Trim(op="trim")], reason="Spaces.", confidence=0.9)
    drafts = TransformationAgent.to_proposals(None, agent_input, TransformationOutput(columns=[one, one]))
    assert [d.payload["source_field"] for d in drafts] == ["COUNTRY"]


class _Loose(TransformationOutput):
    """An answer a strict provider could not return: the steps are plain data that were never checked."""

    def model_dump(self, **kwargs):
        return {"columns": [{"source_field": c.source_field, "steps": c.steps, "reason": c.reason,
                             "confidence": c.confidence} for c in self.columns]}


class _StandIn:
    name = "stand_in"

    def __init__(self, steps):
        self.steps = steps

    def complete(self, **kwargs):
        column = ColumnTransforms.model_construct(source_field="COUNTRY", steps=self.steps, reason="Because.",
                                                  confidence=0.5)
        return LLMResult(output=_Loose.model_construct(columns=[column]), provider="stand_in", model="m",
                         model_version="1", prompt_id=kwargs["prompt_id"], prompt_version=kwargs["prompt_version"],
                         latency_ms=0, raw_ref=None)


@pytest.mark.parametrize("steps", [
    [{"op": "drop_table"}],                                   # an op outside the fixed set
    [{"op": "trim", "sql": "DELETE FROM raw"}],               # a key outside the step's shape
    [{"op": "rename", "to": "x"}, {"op": "trim"}],            # rename is last
    [{"op": "trim"}, {"op": "trim"}],                         # each op once
    [{"op": "cast", "type": "money"}],                        # a cast type outside the set
])
def test_a_step_outside_the_fixed_set_is_discarded_with_a_discard_row_and_no_proposal(db_session, glossary, tmp_path,
                                                                                      steps):
    feed_id = master_mapped(db_session, tmp_path)
    agent_input = transform_input(db_session, feed_id=feed_id)
    end_read(db_session)
    with pytest.raises(ValidationError):
        agent_for(tmp_path, _StandIn(steps)).run(db_session, agent_input)
    [(log_id, reason)] = db_session.execute(
        select(ProposalLogDiscardRow.proposal_log_id, ProposalLogDiscardRow.reason)).all()
    assert reason.startswith("ValidationError") and log_id
    assert ProposalRepository(db_session).list_for_feed(feed_id, "transformation") == []
