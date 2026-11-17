"""RuleRecommenderAgent (step 09, A2, approvers: data steward then checker); phase 6 plan, assumptions 3, 10, 11, 13.

Code keeps only rules whose verdict is `kept`: one `rule` proposal each, on the feed, with the rule's key, SQL,
description and back-test in its payload. A returned or refused rule makes no proposal.
"""

from __future__ import annotations

import uuid

from agents.base import Agent
from agents.rule_recommender import RuleRecommenderAgent
from core.domain.rules import Backtest, ProposedRule, RuleRecommenderInput, RuleRecommenderOutput
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.step_chains import end_read

FEED = uuid.uuid4()


def empty_input(**more):
    return RuleRecommenderInput(feed_id=FEED, feed_code="syn_x", source_name="X", elements=[], feed_templates=[],
                                existing_keys=[], rejected_keys=[], mapping_ids=[], connector=None, history=[], **more)


def rule(key, verdict, **more):
    return ProposedRule(rule_key=key, description="D.", sql="SELECT _row FROM curated", element_keys=["volume"],
                        verdict=verdict, backtest=Backtest(status="not_back_tested"), **more)


def test_the_agent_declares_step_09s_roles_and_autonomy():
    assert (RuleRecommenderAgent.name, RuleRecommenderAgent.autonomy, RuleRecommenderAgent.approver_roles) == (
        "RuleRecommenderAgent", "A2", ("data_steward", "checker"))
    assert issubclass(RuleRecommenderAgent, Agent)


def test_only_a_kept_rule_becomes_a_proposal_with_its_back_test(tmp_path):
    agent = RuleRecommenderAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path))
    output = RuleRecommenderOutput(rules=[rule("a", "kept", template="present"), rule("b", "returned"),
                                          rule("c", "refused", reason="x"), rule("d", None)])
    (draft,) = agent.to_proposals(empty_input(), output)
    assert (draft.kind, draft.feed_id) == ("rule", FEED)
    assert draft.payload == {"rule_key": "a", "sql": "SELECT _row FROM curated", "description": "D.",
                             "element_keys": ["volume"], "template": "present",
                             "backtest": {"status": "not_back_tested", "total_rows": 0, "flagged_rows": 0,
                                          "share": None, "deliveries": []}}


def test_the_mock_provider_answers_the_agents_schema_without_a_verdict(db_session, tmp_path):
    """The mock makes one rule per template offered; verdict, reason and back-test are code's to fill."""
    from core.domain.rules import RuleTemplateOffer

    offer = RuleTemplateOffer(template="present", rule_key="volume_present", description="Volume is present.",
                              sql="SELECT _row FROM curated WHERE volume IS NULL", element_keys=["volume"])
    agent_input = empty_input().model_copy(update={"feed_templates": [offer]})
    end_read(db_session)
    run = RuleRecommenderAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path)).run(
        db_session, agent_input)
    assert [(r.rule_key, r.verdict, r.backtest) for r in run.output.rules] == [("volume_present", None, None)]
    assert run.proposals == []  # no verdict: nothing is kept
