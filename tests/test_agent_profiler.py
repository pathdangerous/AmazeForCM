"""ProfilerAgent (step 02, A2, approver: backend team; phase 4 plan, assumptions 14 and 15) on Agent.run()
with the MockProvider."""

from __future__ import annotations

import uuid

import pytest

from agents.profiler import ProfilerAgent
from core.domain.profiling import ItemCoverage, ProfilerInput, ProfilerOutput, RequirementCoverage, SourceSpec
from core.repositories.feeds import FeedRepository
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.onboarded import COLUMN
from tests.step_chains import end_read


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
    output = ProfilerOutput(summary="s", recommended_outcome="meets", gaps=[], reasons=[])
    [draft] = ProfilerAgent.to_proposals(None, _input(uuid.uuid4(), profile_id), output)
    assert set(draft.payload) == {"profile_id", "summary", "recommended_outcome", "gaps", "reasons"}
    assert draft.payload["profile_id"] == str(profile_id)


@pytest.mark.parametrize("field", ["row_count", "null_rate", "columns", "coverage"])
def test_the_models_text_never_sets_statistics(field):
    with pytest.raises(ValueError):
        ProfilerOutput.model_validate({"summary": "s", "recommended_outcome": "meets", "gaps": [], "reasons": [],
                                       field: 1})
