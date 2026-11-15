"""DiscoveryAssistant (step 17, A2, read only; phase 3 plan, assumptions 38 to 40).

Suggests the glossary elements a requester most likely means by the words their search did not match.
Read only: approver_roles is (), so it cannot define to_proposals and Agent.run() creates no proposal.
Its call is still logged in proposal_log (rule 4, assumption 39).
"""

from __future__ import annotations

from agents.base import Agent
from core.domain.sourcing import AssistantInput, AssistantOutput


class DiscoveryAssistant(Agent):
    name = "DiscoveryAssistant"
    prompt_id = "discovery_assistant"
    autonomy = "A2"
    approver_roles = ()
    input_schema = AssistantInput
    output_schema = AssistantOutput
