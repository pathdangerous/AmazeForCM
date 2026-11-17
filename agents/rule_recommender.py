"""RuleRecommenderAgent (step 09, A2; approvers: the data steward, then the checker); phase 6 plan, assumptions
3, 5, 7, 10, 11 and 13.

The agent sees a registered feed's mapped elements, each with at most 5 rule templates that fit its type, and
answers with SQL rules over the table `curated`. It never sees the glossary or the SQL of the feed's rules. Code
back-tests each answer on the synthetic history and gives it a verdict (Agent.run(after_answer=...)); only a
`kept` rule becomes a `rule` proposal, with its back-test in the payload. A returned or refused rule makes none.
"""

from __future__ import annotations

from agents.base import Agent, AgentProposal
from core.domain.rules import RuleProposalPayload, RuleRecommenderInput, RuleRecommenderOutput


class RuleRecommenderAgent(Agent):
    name = "RuleRecommenderAgent"
    prompt_id = "rule_recommender"
    autonomy = "A2"
    approver_roles = ("data_steward", "checker")
    input_schema = RuleRecommenderInput
    output_schema = RuleRecommenderOutput

    def to_proposals(self, agent_input: RuleRecommenderInput, output: RuleRecommenderOutput) -> list[AgentProposal]:
        return [AgentProposal(kind="rule", feed_id=agent_input.feed_id,
                              payload=RuleProposalPayload(rule_key=rule.rule_key, sql=rule.sql,
                                                          description=rule.description,
                                                          element_keys=rule.element_keys, template=rule.template,
                                                          backtest=rule.backtest).model_dump(mode="json"))
                for rule in output.rules if rule.verdict == "kept" and rule.backtest is not None]
