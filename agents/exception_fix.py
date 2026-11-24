"""ExceptionFixAgent (phase 7.14, A2; approvers: the data steward, and for a waiver then the feed's data owner).

Per exception group of a held load it proposes one fix: a transformation (the existing `transformation` proposal,
marked with its group, steps from the fixed safe set only), a waiver with its reason, a referral to the provider, or
none. It never changes data (rule 1): a person approves each fix; a waiver is applied by the engine to a rerun's NEW
records, never to the held load's. roles_for() gives a waiver both roles, everything else the steward's alone.
"""

from __future__ import annotations

from agents.base import Agent, AgentProposal
from core.domain.exception_fix import ExceptionFixInput, ExceptionFixOutput
from core.domain.transformation import TransformationDraft, TransformationProposalPayload


class ExceptionFixAgent(Agent):
    name = "ExceptionFixAgent"
    prompt_id = "exception_fix"
    autonomy = "A2"
    approver_roles = ("data_steward", "data_owner")
    input_schema = ExceptionFixInput
    output_schema = ExceptionFixOutput

    def roles_for(self, draft: AgentProposal):
        if draft.kind == "exception_fix" and draft.payload.get("action") == "waive":
            return self.approver_roles
        return self.approver_roles[:1]

    def to_proposals(self, agent_input: ExceptionFixInput, output: ExceptionFixOutput) -> list[AgentProposal]:
        groups = {g.group_key: g for g in agent_input.groups}
        seen: set[str] = set()
        drafts = []
        for fix in output.fixes:
            group = groups.get(fix.group_key)
            if group is None or fix.group_key in seen or fix.action == "none" or fix.action in group.rejected_before:
                continue
            seen.add(fix.group_key)
            ref = {"load_id": str(agent_input.load_id), "group_key": group.group_key, "rule_key": group.rule_key,
                   "pattern": group.pattern}
            if fix.action == "transformation":
                element = next((e for e, f in group.source_fields.items() if f == fix.source_field), None)
                if element is None or not fix.steps:
                    continue  # a column the group does not name: no proposal
                checked = TransformationDraft(feed_id=agent_input.feed_id, source_field=fix.source_field,
                                              steps=fix.steps)  # a step outside the set raises: answer discarded
                payload = TransformationProposalPayload(source_field=checked.source_field, element_key=element,
                                                        steps=checked.steps, reason=fix.reason,
                                                        confidence=fix.confidence).model_dump(mode="json")
                drafts.append(AgentProposal(kind="transformation", feed_id=agent_input.feed_id,
                                            payload={**payload, "exception_group": ref}))
                continue
            drafts.append(AgentProposal(kind="exception_fix", feed_id=agent_input.feed_id, payload={
                **ref, "element_keys": group.elements, "action": fix.action, "reason": fix.reason,
                "confidence": fix.confidence}))
        return drafts
