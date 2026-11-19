"""ReviewSummariserAgent (step 11, A1, approver: the data owner); phase 6 plan, assumptions 21 to 23.

A1 assist: the agent drafts the go-live summary and the data owner decides. The payload holds the draft and the
facts code filled (the pinned mapping ids, rule ids and test load, the unmapped columns, the rejected glossary
proposals); the database checks the pins (migration 0018, G6).
"""

from __future__ import annotations

import uuid

from agents.base import Agent
from agents.review_summariser import ReviewSummariserAgent
from core.domain.go_live import ReviewSummaryInput, ReviewSummaryOutput
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.step_chains import end_read

FEED, MAPPING, RULE, LOAD = (uuid.uuid4() for _ in range(4))


def facts(**more):
    base = dict(feed_id=FEED, feed_code="syn_x", source_name="X", owner_id="dev-data-owner", licence="L",
                permitted_use="P", redistribution="R", mapped=[], unmapped_columns=[],
                rejected_glossary=[], rules=[], test_load={"load_id": LOAD, "rows": 3, "stages": [], "flags": {}},
                pending={"glossary": 0, "mapping": 0, "rule": 0}, mapping_ids=[MAPPING], rule_ids=[RULE],
                test_load_id=LOAD)
    return ReviewSummaryInput(**{**base, **more})


def test_the_agent_is_a_1_assist_for_the_data_owner():
    assert (ReviewSummariserAgent.name, ReviewSummariserAgent.autonomy, ReviewSummariserAgent.approver_roles) == (
        "ReviewSummariserAgent", "A1", ("data_owner",))
    assert issubclass(ReviewSummariserAgent, Agent)


def a_feed(db_session):
    """The proposal points at a feed row."""
    from core.repositories.feeds import FeedRepository

    return FeedRepository(db_session).create_candidate(code="syn_summary_feed", name="Summary feed").id


def test_one_review_summary_proposal_with_the_pins_and_the_facts_code_filled(db_session, tmp_path):
    feed = a_feed(db_session)
    agent_input = facts(feed_id=feed, unmapped_columns=["note"],
                        rejected_glossary=[{"source_field": "maturity", "note": "No."}])
    end_read(db_session)
    run = ReviewSummariserAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path)).run(
        db_session, agent_input)
    assert isinstance(run.output, ReviewSummaryOutput)
    (proposal,) = run.proposals
    payload = proposal.payload
    assert (proposal.kind, proposal.feed_id, proposal.required_approver_roles, proposal.autonomy) == (
        "review_summary", feed, ["data_owner"], "A1")
    assert (payload["mapping_ids"], payload["rule_ids"], payload["test_load_id"]) == (
        [str(MAPPING)], [str(RULE)], str(LOAD))
    assert payload["unmapped_columns"] == ["note"]
    assert payload["rejected_glossary"] == [{"source_field": "maturity", "note": "No."}]
    assert payload["summary"] == run.output.summary and payload["highlights"] == run.output.highlights


def test_the_input_and_the_rendered_prompt_never_mention_visibility():
    """Phase 7.8, S8: the owner chooses visibility at go-live, after the summary is drafted (prompt v2)."""
    from agents.prompt_loader import PromptLibrary
    from agents.registry import PROMPT_VERSIONS

    assert "visibility" not in ReviewSummaryInput.model_fields
    assert PROMPT_VERSIONS["review_summariser"] == "v2"
    messages = PromptLibrary().get("review_summariser").render(facts(unmapped_columns=["note"]))
    rendered = " ".join(m.text() for m in messages).lower()
    assert "visibility" not in rendered and "visible" not in rendered


def test_the_mock_summary_names_the_unmapped_columns_and_rejected_proposals(db_session, tmp_path):
    agent_input = facts(feed_id=a_feed(db_session), unmapped_columns=["note", "venue"],
                        rejected_glossary=[{"source_field": "maturity", "note": "No."}])
    end_read(db_session)
    run = ReviewSummariserAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path)).run(
        db_session, agent_input)
    text = run.output.summary + " ".join(run.output.risks)
    assert "note" in text and "venue" in text and "maturity" in text
