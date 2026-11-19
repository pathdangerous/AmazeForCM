"""ProfilerAgent (step 02, A2, approver: backend team; phase 4 plan, assumptions 14 and 15) on Agent.run()
with the MockProvider."""

from __future__ import annotations

import uuid

import pytest

from agents.profiler import ProfilerAgent
from core.domain.profiling import ItemCoverage, ProfilerInput, ProfilerOutput, RequirementCoverage, SourceSpec
from core.domain.profiling import DuplicateKey
from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.steps.s02_profile import key_facts, record_summary, summary_input
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.onboarded import COLUMN
from tests.step_chains import end_read, profile_feed
from tests.test_s00_intake import DEMO, catalogue_for, confirm, inspect


def _input(feed_id, profile_id=None, *, missing=()):
    requirement = uuid.uuid4()
    items = [ItemCoverage(item="volume", kind="element", declared=True, column="qty", in_file=True, null_rate=0.0,
                          distinct_count=1)]
    items += [ItemCoverage(item=m, kind="element", declared=False, column=None, in_file=False, null_rate=None,
                           distinct_count=None) for m in missing]
    return ProfilerInput(
        feed_id=feed_id, feed_code="syn_a", profile_id=profile_id or uuid.uuid4(), outcome="profiled", row_count=1,
        columns=[COLUMN], detail=None, spec=SourceSpec(source_code="syn_a", elements=["volume"], other_fields=[],
                                                      columns={"qty": "volume"}),
        coverage=[RequirementCoverage(requirement_id=requirement, items=items, missing=list(missing))],
        requirement_titles={requirement: "volume"})


def test_the_agents_declaration():
    assert (ProfilerAgent.name, ProfilerAgent.prompt_id, ProfilerAgent.autonomy, ProfilerAgent.approver_roles) == (
        "ProfilerAgent", "profiler", "A2", ("backend_team",))


def test_one_profile_summary_proposal_for_the_backend_team(db_session, tmp_path):
    feed = FeedRepository(db_session).create_candidate(code="syn_a", name="A")
    agent = ProfilerAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))
    end_read(db_session)
    run = agent.run(db_session, _input(feed.id, missing=("isin",)))
    [proposal] = run.proposals
    assert (proposal.kind, proposal.feed_id, proposal.requirement_id, proposal.required_approver_roles,
            proposal.autonomy) == ("profile_summary", feed.id, None, ["backend_team"], "A2")
    assert proposal.payload["recommended_outcome"] == "meets_with_gaps"
    assert proposal.payload["gaps"] == ["isin is not in the file"]


def test_the_proposal_names_the_profile_and_carries_no_statistics():
    profile_id = uuid.uuid4()
    output = ProfilerOutput(summary="s", recommended_outcome="meets", gaps=[], reasons=[], key_columns=[],
                            key_reason="r", confidence=0.3)
    [draft] = ProfilerAgent.to_proposals(None, _input(uuid.uuid4(), profile_id), output)
    assert set(draft.payload) == {"profile_id", "summary", "recommended_outcome", "gaps", "reasons", "key_columns",
                                  "key_reason", "confidence", "duplicate_key", "unknown_key_columns"}
    assert draft.payload["profile_id"] == str(profile_id)


@pytest.mark.parametrize("field", ["row_count", "null_rate", "columns", "coverage"])
def test_the_models_text_never_sets_statistics(field):
    with pytest.raises(ValueError):
        ProfilerOutput.model_validate({"summary": "s", "recommended_outcome": "meets", "gaps": [], "reasons": [],
                                       "key_columns": [], "key_reason": "r", "confidence": 0.5, field: 1})


# ---------------------------------------------------------------- phase 7.8, task 3.2: key columns (assumption 25)


def master_feed(db_session, blobs):
    """The security master (D8 file) uploaded, confirmed and profiled; its feed id."""
    upload = inspect(blobs, DEMO.read_bytes(), DEMO.name)
    db_session.commit()
    uploaded = confirm(db_session, blobs, upload.upload_id, filename=DEMO.name, feed_code="demo_security_master")
    db_session.commit()
    profile_feed(db_session, feed_id=uploaded.feed_id, catalogue=catalogue_for(db_session), blob_store=blobs,
                 profiled_by="dev-backend-team")
    return uploaded.feed_id


def test_the_mock_proposes_ticker_name_and_code_counts_its_duplicates(db_session, tmp_path):
    blobs = LocalBlobStore(tmp_path / "blob")
    feed_id = master_feed(db_session, blobs)
    agent = ProfilerAgent(provider=MockProvider(), blob_store=blobs)
    agent_input = summary_input(db_session, feed_id=feed_id)
    end_read(db_session)
    summary = record_summary(db_session, agent=agent, agent_input=agent_input, blob_store=blobs)
    payload = ProposalRepository(db_session).get(summary.proposal_id).payload
    assert (payload["key_columns"], payload["confidence"], payload["unknown_key_columns"]) == (["TICKER_NAME"], 0.9, [])
    assert payload["key_reason"]
    assert payload["duplicate_key"] == {"columns": ["TICKER_NAME"], "extra_rows": 1, "examples": ["100001 SY Equity"]}
    assert payload["recommended_outcome"] == "meets_with_gaps"
    for name in ("COUNTRY", "REGION", "F_PERIOD", "RECORD_DATE", "BEST_EPS_1_WEEK_PERCENT_CHANGE", "SOURCE",
                 "TICKER_NAME"):
        assert name in payload["summary"], name


def _output(key_columns):
    return ProfilerOutput(summary="s", recommended_outcome="meets", gaps=[], reasons=[], key_columns=key_columns,
                          key_reason="r", confidence=0.5)


def test_an_unknown_key_column_is_dropped_and_listed(tmp_path):
    path = tmp_path / "t.csv"
    path.write_bytes(b"id,v\n1,a\n1,b\n2,c\n")
    out = key_facts(_output(["nope", "id"]), columns=["id", "v"], file=path)
    assert (out.key_columns, out.unknown_key_columns) == (["id"], ["nope"])
    assert out.duplicate_key == DuplicateKey(columns=["id"], extra_rows=1, examples=["1"])


def test_no_key_means_no_duplicate_count_and_a_unique_key_has_none(tmp_path):
    path = tmp_path / "t.csv"
    path.write_bytes(b"id,v\n1,a\n2,b\n")
    assert key_facts(_output([]), columns=["id", "v"], file=path).duplicate_key is None
    assert key_facts(_output(["nope"]), columns=["id", "v"], file=path).duplicate_key is None
    assert key_facts(_output(["id"]), columns=["id", "v"], file=path).duplicate_key is None
    assert key_facts(_output(["id"]), columns=["id", "v"], file=None).duplicate_key is None


def test_the_code_counted_fields_are_left_empty_by_the_model_and_confidence_is_a_fraction():
    out = _output(["id"])
    assert (out.duplicate_key, out.unknown_key_columns) == (None, [])
    with pytest.raises(ValueError):
        ProfilerOutput(summary="s", recommended_outcome="meets", gaps=[], reasons=[], key_columns=[], key_reason="r",
                       confidence=1.5)


def test_the_mock_key_rules():
    from ports.llm.mock_rules import profiler

    def col(name, distinct, **kw):
        return {"name": name, "distinct_count": distinct, "constant": False, "all_empty": False, **kw}

    base = {"outcome": "profiled", "row_count": 4, "coverage": [], "issues": []}
    named = profiler({**base, "columns": [col("X", 1, constant=True), col("SECURITY_ISIN", 4), col("q", 4)]})
    assert (named["key_columns"], named["confidence"]) == (["SECURITY_ISIN"], 0.9)
    unique = profiler({**base, "columns": [col("a", 3), col("b", 4), col("c", 4)]})
    assert (unique["key_columns"], unique["confidence"]) == (["b"], 0.6)
    none = profiler({**base, "columns": [col("a", 3), col("b", 2)]})
    assert (none["key_columns"], none["confidence"]) == ([], 0.3)
    assert none["recommended_outcome"] == "meets"
