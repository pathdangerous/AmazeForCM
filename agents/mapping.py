"""MappingAgent (step 06, A2, approver: data steward) and MappingEvaluator (read only); phase 5 plan,
assumptions 21 to 23, 29 and 37.

The MappingAgent sees a registered feed's columns to map, each with at most 10 candidates, and answers per
column zero, one or several element keys, best first. Code keeps only candidates (kept_elements): none
left is no_element_back_to_05 (no proposal); one is a pending mapping proposal recommending it; several
are a tie_to_steward proposal with no recommendation. A proposal that would change nothing (its only key
is the column's current element) is not created.

The MappingEvaluator is the same prompt, input and output on the gold set: read only, so Agent.run() logs
each call and creates no proposal.
"""

from __future__ import annotations

from agents.base import Agent, AgentProposal
from core.domain.mapping import MappingInput, MappingOutput, MappingProposalPayload
from core.steps.candidates import kept_elements


class MappingAgent(Agent):
    name = "MappingAgent"
    prompt_id = "mapping"
    autonomy = "A2"
    approver_roles = ("data_steward",)
    input_schema = MappingInput
    output_schema = MappingOutput

    def to_proposals(self, agent_input: MappingInput, output: MappingOutput) -> list[AgentProposal]:
        columns = {c.name: c for c in agent_input.columns}
        seen: set[str] = set()
        drafts = []
        for answer in output.answers:
            column = columns.get(answer.column)
            if column is None or answer.column in seen:
                continue
            seen.add(answer.column)
            kept = kept_elements(column, answer.elements)
            if not kept or kept == [column.current]:
                continue
            payload = MappingProposalPayload(source_field=column.name, column_type=column.type, candidates=kept,
                                             recommended=kept[0] if len(kept) == 1 else None, reason=answer.reason)
            drafts.append(AgentProposal(kind="mapping", feed_id=agent_input.feed_id,
                                        payload=payload.model_dump(mode="json")))
        return drafts


class MappingEvaluator(Agent):
    name = "MappingEvaluator"
    prompt_id = "mapping"
    autonomy = "A2"
    approver_roles = ()
    input_schema = MappingInput
    output_schema = MappingOutput
