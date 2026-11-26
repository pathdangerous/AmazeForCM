"""TransformationAgent (step 10, A2, approver: the data steward); phase 7.8 plan, assumptions 27 and 28.

The agent sees a registered feed's mapped columns with the facts code found in the file (placeholders, text dates,
types), and no values unless SEND_SAMPLE_VALUES is true. It answers, per column, an ordered list of steps taken only
from the fixed safe set (trim, placeholder_to_null, parse_date, cast, rename), or none. Each column with steps becomes
one `transformation` proposal. A step outside the set, or steps in a wrong order, fail the domain model here, so the
whole answer is discarded by Agent.run() (a discard row, no proposal). The engine turns approved steps into fixed SQL
templates (core/engine/transform.py); nothing the model writes is ever run.
"""

from __future__ import annotations

from agents.base import Agent, AgentProposal
from core.domain.transformation import (
    TransformationDraft,
    TransformationInput,
    TransformationOutput,
    TransformationProposalPayload,
)


class TransformationAgent(Agent):
    name = "TransformationAgent"
    prompt_id = "transformation"
    autonomy = "A2"
    approver_roles = ("data_steward",)
    input_schema = TransformationInput
    output_schema = TransformationOutput

    def to_proposals(self, agent_input: TransformationInput, output: TransformationOutput) -> list[AgentProposal]:
        columns = {c.source_field: c for c in agent_input.columns}
        rejected = {r.source_field: set(r.ops) for r in agent_input.rejected}  # T5: a person said no to these ops
        seen: set[str] = set()
        drafts = []
        for answer in output.columns:
            column = columns.get(answer.source_field)
            steps = [s for s in answer.steps  # (a step may still be raw here: its validation is below)
                     if (s.get("op") if isinstance(s, dict) else s.op) not in rejected.get(answer.source_field, ())]
            if column is None or answer.source_field in seen or not steps:
                continue
            seen.add(answer.source_field)
            checked = TransformationDraft(feed_id=agent_input.feed_id, source_field=column.source_field,
                                          steps=steps)  # a step outside the set raises: the answer is discarded
            payload = TransformationProposalPayload(
                source_field=column.source_field, element_key=column.element_key, steps=checked.steps,
                reason=answer.reason, confidence=answer.confidence)
            drafts.append(AgentProposal(kind="transformation", feed_id=agent_input.feed_id,
                                        payload=payload.model_dump(mode="json")))
        return drafts
