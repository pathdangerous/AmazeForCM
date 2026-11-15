"""ProfilerAgent (step 02, A2, approver: backend team; phase 4 plan, assumptions 14 and 15).

Summarises one stored profile and recommends an outcome: one profile_summary proposal, linked to the
feed, naming the profile. Code computes, the agent summarises: the payload holds the agent's text and
its recommendation only, never a number. The backend team's sign-off (step 03) decides it: approving it
means agreeing with the recommended outcome (decision D1).
"""

from __future__ import annotations

from agents.base import Agent, AgentProposal
from core.domain.profiling import ProfilerInput, ProfilerOutput


class ProfilerAgent(Agent):
    name = "ProfilerAgent"
    prompt_id = "profiler"
    autonomy = "A2"
    approver_roles = ("backend_team",)
    input_schema = ProfilerInput
    output_schema = ProfilerOutput

    def to_proposals(self, agent_input: ProfilerInput, output: ProfilerOutput) -> list[AgentProposal]:
        return [AgentProposal(kind="profile_summary", feed_id=agent_input.feed_id,
                              payload={"profile_id": str(agent_input.profile_id), **output.model_dump(mode="json")})]
