"""IntentAgent (S1, A2, approver: requester; phase 3 plan, assumption 27).

Proposes the scope of the search for a source: public or private, who provides, where to search. One
scope proposal per run. where_to_search keeps only channels the agent was given; if none is left, every
given channel of the proposed visibility.
"""

from __future__ import annotations

from agents.base import Agent, AgentProposal
from core.domain.sourcing import IntentInput, IntentOutput


class IntentAgent(Agent):
    name = "IntentAgent"
    prompt_id = "intent"
    autonomy = "A2"
    approver_role = "requester"
    input_schema = IntentInput
    output_schema = IntentOutput

    def to_proposals(self, agent_input: IntentInput, output: IntentOutput) -> list[AgentProposal]:
        allowed = [c.channel for c in agent_input.channels if c.visibility == output.visibility]
        chosen = [c for c in output.where_to_search if c in allowed] or allowed
        payload = {**output.model_dump(mode="json"), "where_to_search": chosen}
        return [AgentProposal(kind="scope", payload=payload, requirement_id=agent_input.requirement_id)]
