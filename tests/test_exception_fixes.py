"""Phase 7.14 T2: ExceptionFixAgent and the decisions on its fixes (plan docs/plans/phase-7.14.md, sections 2.2, 2.3).

Per exception group of a held load the agent proposes one fix: a transformation (the existing transformation proposal,
marked with its group), a waiver (steward, then the registered owner) or a referral to the provider (steward). A group
it cannot fix is no_fix_proposed, with the reason. A person may add a fix by hand (no model call, no log row). Only an
approved fix is recorded; nothing changes a record. agents/base.py gains roles_for(): every existing agent still gives
its proposals its full declared roles.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest
from sqlalchemy import func, select, text

import agents
from agents.base import Agent, AgentProposal, RolesNotAPrefix
from agents.exception_fix import ExceptionFixAgent
from core.repositories.errors import ExceptionGroupChangedMeanwhile, LoadNotHeld, NotAnApprover
from core.repositories.proposals import ProposalRepository
from core.repositories.tables import ExceptionResolutionRow, ProposalLogEntry, ProposalRow
from core.steps.drafts import draft_config
from core.steps.errors import StepInputError
from core.steps.exceptions import (
    MAX_AGENT_GROUPS,
    add_manual_fix,
    decide_exception_fix,
    exception_fix_input,
    load_exceptions,
    propose_fixes,
)
from core.steps.onboarding import stale_drafts
from core.steps.s10_transform import decide_transformation
from ports.config import Settings
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.conftest import dev_user_for
from tests.configured import VOLUME_RULE, approved_rule, go_live, transformation_proposal, volume_csv
from tests.step_chains import end_read
from tests.test_exceptions_view import HEADER, ID_RULE, ID_SQL, deliver, world  # noqa: F401 (world is a fixture)

STEWARD, OWNER = dev_user_for("data_steward"), dev_user_for("data_owner")
NEGATIVE_RULE = "volume_not_negative"


@pytest.fixture
def agent(tmp_path):
    return ExceptionFixAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "agent-blob"))


def with_rules(world, *rules):
    for key, sql, description in rules:
        approved_rule(world.session, world.feed, key, sql=sql, description=description)
    world.live = go_live(world.session, world.feed)


def a_held_load_with_three_kinds_of_group(world):
    """VOLUME_RULE flags 200000 (pattern 9: refer), NEGATIVE_RULE flags -5 (pattern -9: waive), ID_RULE flags ' int-3'
    (pattern ' a-9', leading space: a trim of instr_id). 6 rows, 3 flagged: 50.0, held."""
    with_rules(world, (NEGATIVE_RULE, "SELECT _row FROM curated WHERE volume < 0", "Volume is not negative."),
               (ID_RULE, ID_SQL, "Instrument ids are internal."))
    rows = ["int-1,2026-11-02,200000", "int-2,2026-11-02,-5", '" int-3",2026-11-02,5',
            "int-4,2026-11-02,5", "int-5,2026-11-02,5", "int-6,2026-11-02,5"]
    return deliver(world, HEADER + "\n".join(rows) + "\n")


def propose(world, agent, load_id, settings: Settings | None = None):
    agent_input = exception_fix_input(world.session, load_id=load_id, settings=settings or Settings(_env_file=None))
    end_read(world.session)
    return propose_fixes(world.session, agent=agent, agent_input=agent_input)


def by_rule(world, load_id):
    return {g.rule_key: g for g in load_exceptions(world.session, load_id).groups}


def calls(world) -> int:
    return world.session.scalar(select(func.count()).select_from(ProposalLogEntry)) or 0


# ---------------------------------------------------------------- roles_for (Q6)


def _agent_classes():
    for module in pkgutil.iter_modules(agents.__path__):
        importlib.import_module(f"agents.{module.name}")
    found, todo = [], list(Agent.__subclasses__())
    while todo:
        cls = todo.pop()
        found.append(cls)
        todo.extend(cls.__subclasses__())
    return found


def test_every_existing_agent_still_gives_its_proposals_its_full_declared_roles():
    classes = [c for c in _agent_classes() if c is not ExceptionFixAgent and c.__module__.startswith("agents.")]
    assert len(classes) >= 10
    draft = AgentProposal(kind="rule", payload={})
    for cls in classes:
        assert Agent.roles_for is cls.roles_for, cls.__name__  # none overrides it
        assert cls.roles_for(cls.__new__(cls), draft) == cls.approver_roles, cls.__name__


def test_the_fix_agent_gives_a_waiver_both_roles_and_the_rest_the_steward(agent):
    assert agent.approver_roles == ("data_steward", "data_owner")
    waive = AgentProposal(kind="exception_fix", payload={"action": "waive"})
    refer = AgentProposal(kind="exception_fix", payload={"action": "refer"})
    trim = AgentProposal(kind="transformation", payload={})
    assert [agent.roles_for(d) for d in (waive, refer, trim)] == [
        ("data_steward", "data_owner"), ("data_steward",), ("data_steward",)]


def test_roles_that_are_not_a_prefix_discard_the_answer_after_the_call_is_logged(world, agent, monkeypatch):
    held = a_held_load_with_three_kinds_of_group(world)
    monkeypatch.setattr(ExceptionFixAgent, "roles_for", lambda self, draft: ("data_owner",))
    before = calls(world)
    with pytest.raises(RolesNotAPrefix):
        propose(world, agent, held.id)
    assert calls(world) == before + 1  # the model was called and logged: roles_for is not on the call path
    discards = world.session.scalar(text("SELECT count(*) FROM proposal_log_discards"))
    assert discards >= 1
    assert world.session.scalar(select(func.count()).select_from(ProposalRow).where(
        ProposalRow.kind == "exception_fix")) == 0


# ---------------------------------------------------------------- the input


def test_the_input_holds_the_open_groups_of_a_held_load_with_the_rule_and_no_values_unless_allowed(world):
    held = a_held_load_with_three_kinds_of_group(world)
    agent_input = exception_fix_input(world.session, load_id=held.id, settings=Settings(_env_file=None))
    groups = {g.rule_key: g for g in agent_input.groups}
    assert set(groups) == {VOLUME_RULE, NEGATIVE_RULE, ID_RULE}
    assert (groups[ID_RULE].pattern, groups[ID_RULE].source_fields, groups[ID_RULE].sql) == (
        " a-9", {"instrument_id": "instr_id"}, ID_SQL)
    assert all(g.samples == [] for g in agent_input.groups)  # SEND_SAMPLE_VALUES is false
    shown = exception_fix_input(world.session, load_id=held.id, settings=Settings(_env_file=None, send_sample_values=True))
    assert {g.rule_key: g.samples for g in shown.groups}[NEGATIVE_RULE] == [{"volume": -5}]  # as stored


def test_the_agent_sees_at_most_the_largest_groups_and_five_samples_each(world):
    with_rules(world, (ID_RULE, ID_SQL, "Instrument ids are internal."))
    rows = [f"q{'.' * n}1,2026-11-02,5" for n in range(1, 26)]  # 25 shapes: 25 groups of one
    rows += [f"x-{n},2026-11-02,5" for n in range(1, 9)]  # one group of 8
    held = deliver(world, HEADER + "\n".join(rows) + "\n")
    agent_input = exception_fix_input(world.session, load_id=held.id,
                                      settings=Settings(_env_file=None, send_sample_values=True))
    assert len(agent_input.groups) == MAX_AGENT_GROUPS == 20
    assert agent_input.groups[0].pattern == "a-9" and len(agent_input.groups[0].samples) == 5


def test_a_load_that_is_not_held_takes_no_fix(world):
    released = deliver(world, volume_csv(10, 1))
    with pytest.raises(LoadNotHeld):
        exception_fix_input(world.session, load_id=released.id, settings=Settings(_env_file=None))


# ---------------------------------------------------------------- propose


def test_one_logged_call_proposes_a_referral_a_waiver_and_a_transformation(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    before = calls(world)
    result = propose(world, agent, held.id)
    assert result.outcome == "fixes_proposed" and calls(world) == before + 1
    proposals = {ProposalRepository(world.session).get(i).payload.get("rule_key")
                 or ProposalRepository(world.session).get(i).payload["exception_group"]["rule_key"]:
                 ProposalRepository(world.session).get(i) for i in result.proposal_ids}
    refer, waive, trim = proposals[VOLUME_RULE], proposals[NEGATIVE_RULE], proposals[ID_RULE]
    assert (refer.kind, refer.payload["action"], refer.required_approver_roles) == (
        "exception_fix", "refer", ["data_steward"])
    assert (waive.kind, waive.payload["action"], waive.required_approver_roles) == (
        "exception_fix", "waive", ["data_steward", "data_owner"])
    assert (trim.kind, trim.payload["source_field"], trim.payload["steps"], trim.required_approver_roles) == (
        "transformation", "instr_id", [{"op": "trim"}], ["data_steward"])
    assert trim.payload["exception_group"]["load_id"] == str(held.id)
    assert {p.proposal_log_id for p in proposals.values()} == {result.log_id}
    assert {g.rule_key: g.state for g in load_exceptions(world.session, held.id).groups} == {
        VOLUME_RULE: "fix_pending", NEGATIVE_RULE: "fix_pending", ID_RULE: "fix_pending"}


def test_a_group_whose_column_has_a_pending_transformation_is_not_asked_about(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    transformation_proposal(world.session, world.feed, "instr_id", [{"op": "trim"}])
    result = propose(world, agent, held.id)
    assert {g.group_key: g.reason for g in result.no_fix}[by_rule(world, held.id)[ID_RULE].group_key] == (
        "transformation_pending")


def test_a_load_with_no_open_group_makes_no_model_call(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    propose(world, agent, held.id)
    before = calls(world)
    result = propose(world, agent, held.id)
    assert result.outcome == "no_fix_proposed" and result.log_id is None and calls(world) == before


def test_a_fix_a_person_rejected_is_not_proposed_again(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    first = propose(world, agent, held.id)
    refer = next(p for p in map(ProposalRepository(world.session).get, first.proposal_ids)
                 if p.payload.get("action") == "refer")
    decide_exception_fix(world.session, proposal_id=refer.id, user=STEWARD, decision="rejected",
                         note="Ask the trading desk, not the provider.")
    again = propose(world, agent, held.id)
    assert again.outcome == "no_fix_proposed"
    assert [(g.group_key, g.reason) for g in again.no_fix] == [(refer.payload["group_key"], "rejected_before")]


def test_a_group_changed_during_the_call_discards_the_answer(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    agent_input = exception_fix_input(world.session, load_id=held.id, settings=Settings(_env_file=None))
    end_read(world.session)
    propose_fixes(world.session, agent=agent, agent_input=agent_input)  # the same groups get their fixes
    end_read(world.session)
    with pytest.raises(ExceptionGroupChangedMeanwhile):
        propose_fixes(world.session, agent=agent, agent_input=agent_input)


# ---------------------------------------------------------------- the decisions


def fixes(world, agent, held):
    result = propose(world, agent, held.id)
    found = {}
    for proposal in map(ProposalRepository(world.session).get, result.proposal_ids):
        found[proposal.payload.get("action") or "transformation"] = proposal
    return found


def resolutions(world):
    return list(world.session.scalars(select(ExceptionResolutionRow)))


def test_a_referral_approved_by_the_steward_is_recorded_as_the_agents(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    refer = fixes(world, agent, held)["refer"]
    decided = decide_exception_fix(world.session, proposal_id=refer.id, user=STEWARD, decision="approved")
    assert (decided.status, decided.waiting_for) == ("approved", None)
    [row] = resolutions(world)
    assert (row.action, row.applied_by, row.decided_by, row.rule_key) == (
        "referred", "agent", "dev-data-steward", VOLUME_RULE)
    assert by_rule(world, held.id)[VOLUME_RULE].state == "referred"


def test_a_waiver_waits_for_the_registered_owner_after_the_steward(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    waive = fixes(world, agent, held)["waive"]
    operator = dev_user_for("backend_team").model_copy(update={"id": "dev-operator",
                                                               "roles": ["data_owner", "data_steward"]})
    first = decide_exception_fix(world.session, proposal_id=waive.id, user=operator, decision="approved")
    assert (first.status, first.waiting_for) == ("pending", "data_owner")  # not the registered owner: one step only
    assert resolutions(world) == [] and by_rule(world, held.id)[NEGATIVE_RULE].state == "waiting_for_owner"
    with pytest.raises(NotAnApprover):
        decide_exception_fix(world.session, proposal_id=waive.id, user=operator, decision="approved")
    done = decide_exception_fix(world.session, proposal_id=waive.id, user=OWNER, decision="approved")
    assert done.status == "approved"
    [row] = resolutions(world)
    assert (row.action, row.decided_by, row.pattern, row.element_keys) == ("waived", "dev-data-owner", "-9", ["volume"])
    assert by_rule(world, held.id)[NEGATIVE_RULE].state == "waived"


def test_a_rejection_needs_a_note_and_the_steward(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    refer = fixes(world, agent, held)["refer"]
    with pytest.raises(StepInputError):
        decide_exception_fix(world.session, proposal_id=refer.id, user=STEWARD, decision="rejected", note=" ")
    with pytest.raises(NotAnApprover):
        decide_exception_fix(world.session, proposal_id=refer.id, user=OWNER, decision="approved")
    decide_exception_fix(world.session, proposal_id=refer.id, user=STEWARD, decision="rejected", note="Not this.")
    assert by_rule(world, held.id)[VOLUME_RULE].state == "open" and resolutions(world) == []


def test_a_transformation_fix_goes_through_the_transformation_decision(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    trim = fixes(world, agent, held)["transformation"]
    stored = decide_transformation(world.session, proposal_id=trim.id, user=STEWARD, decision="approved")
    assert stored.transformation.source_field == "instr_id"
    assert by_rule(world, held.id)[ID_RULE].state == "transformation_approved"


# ---------------------------------------------------------------- by hand


def test_a_person_adds_a_fix_with_no_model_call(world):
    held = a_held_load_with_three_kinds_of_group(world)
    groups = by_rule(world, held.id)
    before = calls(world)
    waive = add_manual_fix(world.session, load_id=held.id, group_key=groups[VOLUME_RULE].group_key, action="waive",
                           reason="Block trades are real.", user=STEWARD)
    trim = add_manual_fix(world.session, load_id=held.id, group_key=groups[ID_RULE].group_key, action="transformation",
                          reason="Leading spaces.", user=STEWARD, steps=[{"op": "trim"}])
    assert calls(world) == before
    assert (waive.origin, waive.author, waive.proposal_log_id, waive.required_approver_roles) == (
        "manual", "dev-data-steward", None, ["data_steward", "data_owner"])
    assert (trim.kind, trim.origin, trim.payload["exception_group"]["group_key"]) == (
        "transformation", "manual", groups[ID_RULE].group_key)
    decide_exception_fix(world.session, proposal_id=waive.id, user=STEWARD, decision="approved")
    decide_exception_fix(world.session, proposal_id=waive.id, user=OWNER, decision="approved")
    assert [r.applied_by for r in resolutions(world)] == ["manual"]


def test_a_manual_fix_is_refused_for_an_unknown_group_or_a_non_steward(world):
    held = a_held_load_with_three_kinds_of_group(world)
    with pytest.raises(StepInputError):
        add_manual_fix(world.session, load_id=held.id, group_key="0000000000000000", action="refer", reason="x",
                       user=STEWARD)
    with pytest.raises(NotAnApprover):
        add_manual_fix(world.session, load_id=held.id, group_key=by_rule(world, held.id)[VOLUME_RULE].group_key,
                       action="refer", reason="x", user=OWNER)


# ---------------------------------------------------------------- 7.13 (plan, section 2.3)


def test_a_chain_never_builds_on_an_exception_fix_and_rejecting_one_makes_nothing_stale(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    trim = fixes(world, agent, held)["transformation"]
    assert "instr_id" not in {t.source_field for t in draft_config(world.session, world.feed).transformations}
    decide_transformation(world.session, proposal_id=trim.id, user=STEWARD, decision="rejected", note="No.")
    assert stale_drafts(world.session, world.feed) == []
    assert world.session.scalar(text("SELECT count(*) FROM approvals WHERE decided_by = 'system'")) == 0


def test_a_rule_draft_says_the_transformation_version_it_was_tested_on_known_gap_g1(world):
    from core.domain.onboarding import TestedOn
    from core.steps.onboarding import transformations_tested_on
    from tests.configured import approved_transformation, rule_proposal

    approved_transformation(world.session, world.feed, "traded_qty", [{"op": "trim"}])  # version 1
    rule = rule_proposal(world.session, world.feed, "volume_small", sql="SELECT _row FROM curated WHERE volume > 5")
    approved_transformation(world.session, world.feed, "traded_qty", [{"op": "trim"}, {"op": "cast", "type": "integer"}])
    assert transformations_tested_on(world.session, rule) == [TestedOn(column="traded_qty", version=1, current_version=2)]
    draft = transformation_proposal(world.session, world.feed, "instr_id", [{"op": "trim"}])
    on_draft = rule_proposal(world.session, world.feed, "id_known", sql=ID_SQL)
    ProposalRepository(world.session).add_bases(on_draft.id, [draft.id], feed_id=world.feed)
    assert transformations_tested_on(world.session, on_draft) == [
        TestedOn(column="instr_id", version=None, current_version=None)]
