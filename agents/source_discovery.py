"""SourceDiscoveryAgent (S2, A2, approver: requester; phase 3 plan, assumptions 30 and 31).

Ranks the catalogue sources the step found in scope and gives a reason for each. One source_list
proposal per ranked source, in rank order. A source code that is not among the candidates (invented)
is dropped, and a repeated one is kept once. Coverage and conditions are copied from the candidate
(the catalogue), never taken from the model.
"""

from __future__ import annotations

from agents.base import Agent, AgentProposal
from core.domain.sourcing import SourceDiscoveryInput, SourceDiscoveryOutput


class SourceDiscoveryAgent(Agent):
    name = "SourceDiscoveryAgent"
    prompt_id = "source_discovery"
    autonomy = "A2"
    approver_roles = ("requester",)
    input_schema = SourceDiscoveryInput
    output_schema = SourceDiscoveryOutput

    def to_proposals(self, agent_input: SourceDiscoveryInput, output: SourceDiscoveryOutput) -> list[AgentProposal]:
        candidates = {c.code: c for c in agent_input.candidates}
        drafts: list[AgentProposal] = []
        for ranked in output.ranked:
            candidate = candidates.pop(ranked.source_code, None)  # pop: a repeat finds nothing
            if candidate is None:
                continue
            drafts.append(AgentProposal(kind="source_list", requirement_id=agent_input.requirement_id, payload={
                "source_code": candidate.code, "rank": len(drafts) + 1, "reason": ranked.reason,
                "coverage": candidate.coverage.model_dump(mode="json"),
                "conditions": candidate.conditions.model_dump(mode="json"),
            }))
        return drafts
