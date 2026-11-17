"""GlossaryBuilderAgent (step 05, A2, approver: glossary owner; phase 5 plan, assumptions 13 to 16).

Sees one feed's new columns, each with at most 10 candidate elements (never the whole glossary), and
answers per column: a candidate it is the same as, a new element, or nothing. Code names the outcome
(candidates.classify_element): proposed, duplicate_alias or conflict_to_owner, one pending
glossary_element proposal per answered column, linked to the feed. A column with no answer, or whose
same_as was not a candidate, gets no proposal and is left to step 06. A column whose option is not valid
(its name gives no alias) gets none either; step 05 records why (candidates.glossary_payloads).
"""

from __future__ import annotations

from agents.base import Agent, AgentProposal
from core.domain.mapping import GlossaryBuilderInput, GlossaryBuilderOutput
from core.steps.candidates import glossary_payloads


class GlossaryBuilderAgent(Agent):
    name = "GlossaryBuilderAgent"
    prompt_id = "glossary_builder"
    autonomy = "A2"
    approver_roles = ("glossary_owner",)
    input_schema = GlossaryBuilderInput
    output_schema = GlossaryBuilderOutput

    def to_proposals(self, agent_input: GlossaryBuilderInput, output: GlossaryBuilderOutput) -> list[AgentProposal]:
        payloads, _ = glossary_payloads(agent_input, output)
        return [AgentProposal(kind="glossary_element", feed_id=agent_input.feed_id,
                              payload=payload.model_dump(mode="json")) for payload in payloads]
