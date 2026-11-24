"""Phase 7.13, T1c: "Run all agents" (plan, decisions 4 to 6).

The session's run_mode becomes chain; after the one click on the profile (it registers the feed), the worker runs the
glossary builder, the mapping agent, the transformation agent and the rule recommender in turn, each on the drafts
before it, with no click in between. It stops only at the model-call cap or a failed call. Nothing becomes
configuration: no mapping, transformation or rule row exists until a person approves.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from core.repositories.errors import StepNotEnterable
from core.repositories.proposals import ProposalRepository
from core.steps.onboarding import CHAIN_STEPS, enter_step, run_all
from ports.llm.base import LLMUnavailable
from ports.llm.mock import MockProvider
from tests.test_onboarding import OPERATOR, approve, current, events, make_world, work


class FailsOnce:
    """The mock, except that the first call with `prompt_id` fails."""

    name = "fails-once"

    def __init__(self, prompt_id: str) -> None:
        self.inner, self.prompt_id, self.failed = MockProvider(), prompt_id, False

    def complete(self, **kw):
        if kw["prompt_id"] == self.prompt_id and not self.failed:
            self.failed = True
            raise LLMUnavailable("synthetic outage")
        return self.inner.complete(**kw)


def configuration_rows(s, feed_id) -> dict[str, int]:
    return {t: s.scalar(text(f"SELECT count(*) FROM {t} WHERE feed_id = :f"), {"f": feed_id})
            for t in ("mappings", "transformations", "rules")}


def chain_on(w) -> None:
    run_all(w.session, session_id=w.session_id, user=OPERATOR)
    w.session.commit()


def test_after_the_profile_approval_every_agent_drafts_with_no_click_and_nothing_is_configuration(db_session,
                                                                                                glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    chain_on(w)
    assert current(w).run_mode == "chain" and [e.step for e in events(w, "chain_started")] == ["profile"]
    work(w)
    approve(w)  # the one click: the profile registers the feed

    ran = work(w)  # the worker, with no click

    assert [r.step for r in ran] == list(CHAIN_STEPS)
    assert all(r.status == "done" for r in ran), ran
    sess = current(w)
    assert sess.current_step == "glossary", "the cursor stays at the first step a person has not approved"
    assert all(sess.step_states[step] in ("ready", "needs_decision") for step in CHAIN_STEPS), sess.step_states
    assert [e.step for e in events(w, "chain_done")] == ["rules"]
    assert configuration_rows(db_session, w.feed_id) == {"mappings": 0, "transformations": 0, "rules": 0}
    proposals = ProposalRepository(db_session)
    for kind, agent in (("glossary_element", "GlossaryBuilderAgent"), ("mapping", "MappingAgent"),
                        ("transformation", "TransformationAgent"), ("rule", "RuleRecommenderAgent")):
        drafts = proposals.list_for_feed(w.feed_id, kind, "pending")
        assert drafts, kind
        for d in drafts:  # one logged call per draft, made by the step's own agent
            logged = db_session.execute(text("SELECT agent_name FROM proposal_log WHERE id = :i"),
                                        {"i": d.proposal_log_id}).scalar_one()
            assert (d.origin, logged) == ("agent", agent)
    assert sess.model_calls == 5  # profile, glossary, mapping, transformations, rules


def test_the_chain_stops_at_the_model_call_cap(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path, max_model_calls_per_onboarding=3)
    chain_on(w)
    work(w)
    approve(w)
    work(w)
    sess = current(w)
    assert sess.model_calls == 3  # profile, glossary, mapping: the transformation agent was not asked
    assert sess.step_states["transformations"] == "failed" and sess.step_states["rules"] == "waiting"
    [stop] = events(w, "chain_stopped")
    assert (stop.step, stop.detail["reason"]) == ("transformations", "call_cap_reached")
    assert not events(w, "chain_done")


def test_a_failed_call_stops_the_chain_and_entering_the_step_again_resumes_it(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path, provider=FailsOnce("mapping"))
    chain_on(w)
    work(w)
    approve(w)
    work(w)
    sess = current(w)
    assert sess.step_states["mapping"] == "failed" and sess.step_states["transformations"] == "waiting"
    [stop] = events(w, "chain_stopped")
    assert (stop.step, stop.detail["reason"]) == ("mapping", "agent_failed")

    enter_step(db_session, session_id=w.session_id, step="mapping", user=OPERATOR)  # a step ahead of the cursor
    db_session.commit()
    work(w)
    assert all(current(w).step_states[step] in ("ready", "needs_decision") for step in CHAIN_STEPS)
    assert [e.step for e in events(w, "chain_done")] == ["rules"]


def test_run_all_agents_is_one_click_once(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    chain_on(w)
    with pytest.raises(StepNotEnterable):
        run_all(db_session, session_id=w.session_id, user=OPERATOR)
    db_session.rollback()


def test_run_all_agents_after_the_profile_drafts_from_the_step_the_session_is_on(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    work(w)
    approve(w)  # step by step: the glossary step is entered and its agent answers
    work(w)
    assert current(w).step_states["glossary"] in ("ready", "needs_decision")
    chain_on(w)
    ran = work(w)
    assert [r.step for r in ran] == ["mapping", "transformations", "rules"]
    assert configuration_rows(db_session, w.feed_id) == {"mappings": 0, "transformations": 0, "rules": 0}
