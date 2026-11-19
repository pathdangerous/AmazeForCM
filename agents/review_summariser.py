"""ReviewSummariserAgent (step 11, A1 assist; approver: the data owner); phase 6 plan, assumptions 21 to 23.

The agent drafts the go-live summary from facts code gathered: what is mapped, what is not (note 4), the glossary
proposals that were rejected (note 4), the rules and their back-test, and the test load. The data owner decides.
The proposal's payload holds the draft, those facts and the pins (mapping ids, rule ids, test load) the database
checks the go-live against.
"""

from __future__ import annotations

from agents.base import Agent, AgentProposal
from core.domain.go_live import ReviewSummaryInput, ReviewSummaryOutput, ReviewSummaryPayload


class ReviewSummariserAgent(Agent):
    name = "ReviewSummariserAgent"
    prompt_id = "review_summariser"
    autonomy = "A1"
    approver_roles = ("data_owner",)
    input_schema = ReviewSummaryInput
    output_schema = ReviewSummaryOutput

    def to_proposals(self, agent_input: ReviewSummaryInput, output: ReviewSummaryOutput) -> list[AgentProposal]:
        payload = ReviewSummaryPayload(
            summary=output.summary, highlights=output.highlights, risks=output.risks,
            unmapped_columns=agent_input.unmapped_columns, rejected_glossary=agent_input.rejected_glossary,
            mapping_ids=sorted(agent_input.mapping_ids), rule_ids=sorted(agent_input.rule_ids),
            transformation_ids=sorted(agent_input.transformation_ids), test_load_id=agent_input.test_load_id)
        return [AgentProposal(kind="review_summary", feed_id=agent_input.feed_id, payload=payload.model_dump(mode="json"))]
