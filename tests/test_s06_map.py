"""Step 06 map (core/steps/s06_map.py; phase 5 plan, assumptions 21 to 33, sections 5.1 to 5.4).

Outcomes (CLAUDE.md): mapped (the steward's approval stored it), tie_to_steward, no_element_back_to_05.
A pending recommendation is not an outcome (nobody has decided). The input is the registered feed's latest
profile; the spec's hint is one candidate among others; an approved mapping is the link later phases read.
"""

from __future__ import annotations

import pytest

from agents.mapping import MappingAgent
from core.repositories.configuration import MappingRepository
from core.repositories.errors import (
    ElementAlreadyMapped,
    FeedNotRegistered,
    MappingAlreadyProposed,
    NotAnApprover,
    NothingToMap,
    ProposalAlreadyDecided,
)
from core.repositories.feeds import FeedRepository
from core.repositories.glossary_runs import GlossaryRunRepository
from core.repositories.proposals import ProposalRepository
from core.steps.errors import ChoiceNeeded, ChoiceNotOffered
from core.steps.s06_map import Mapped, NoElementBackTo05, TieToSteward, decide_mapping, mapping_input, record_mappings
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.conftest import dev_user_for
from tests.onboarded import registered_feed
from tests.stand_in_agents import FixedProvider
from tests.step_chains import end_read
from tests.test_s05_glossary import BOND_COLUMNS, BOND_SPEC

SAM = dev_user_for("data_steward")
GLENN = dev_user_for("glossary_owner")


@pytest.fixture
def bond(db_session, glossary):
    feed = FeedRepository(db_session).create_candidate(code="syn_bond_terms", name="Bond terms")
    return registered_feed(db_session, feed.id, spec=BOND_SPEC, columns=BOND_COLUMNS)


def agent(tmp_path, provider=None):
    return MappingAgent(provider=provider or MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))


def propose(db_session, feed, tmp_path, *, column=None, provider=None):
    agent_input = mapping_input(db_session, feed_id=feed.id, column=column)
    end_read(db_session)
    return record_mappings(db_session, agent=agent(tmp_path, provider), agent_input=agent_input, user=SAM)


def pending(db_session, feed, column):
    return ProposalRepository(db_session).pending_for_column("mapping", feed.id, column)


def decide(db_session, proposal, *, user=SAM, decision="approved", choice=None, note=None):
    return decide_mapping(db_session, proposal_id=proposal.id, user=user, decision=decision, choice=choice, note=note)


# ---------------------------------------------------------------- the three outcomes (section 5.4)


def test_mapped_when_the_steward_approves_a_candidate(db_session, bond, tmp_path):
    proposed = propose(db_session, bond, tmp_path)
    assert {(r.source_field, r.element_key) for r in proposed.recommended} == {
        ("instr_id", "instrument_id"), ("matures_on", "maturity_date"), ("cpn", "coupon_rate"),
        ("venue_mic_code", "exchange_mic")}
    result = decide(db_session, pending(db_session, bond, "cpn"))
    assert isinstance(result, Mapped) and result.outcome == "mapped"
    assert (result.mapping.source_field, result.mapping.element_key, result.mapping.version) == ("cpn", "coupon_rate", 1)


def test_tie_to_steward_when_several_elements_fit_and_the_steward_must_choose(db_session, bond, tmp_path):
    proposed = propose(db_session, bond, tmp_path)
    [tie] = [r for r in proposed.results if r.outcome == "tie_to_steward"]
    assert isinstance(tie, TieToSteward)
    assert (tie.source_field, tie.candidates) == ("date", ["issue_date", "maturity_date", "price_date"])
    assert ProposalRepository(db_session).get(tie.proposal_id).payload["recommended"] is None


def test_no_element_back_to_05_queues_a_glossary_run_for_the_column(db_session, bond, tmp_path):
    proposed = propose(db_session, bond, tmp_path)
    back = {r.source_field: r for r in proposed.results if r.outcome == "no_element_back_to_05"}
    assert {c: r.reason for c, r in back.items()} == {"rating": "no_candidate", "maturity": "type_does_not_fit"}
    assert all(isinstance(r, NoElementBackTo05) for r in back.values())
    run = GlossaryRunRepository(db_session).active(bond.id)
    assert (run.reason, run.columns, run.requested_by) == ("sent_back", ["rating", "maturity"], "dev-data-steward")
    assert {r.glossary_run_id for r in back.values()} == {run.id}
    # the third reason: the steward rejected every candidate of the column
    decide(db_session, pending(db_session, bond, "venue_mic_code"), decision="rejected", note="Not the venue.")
    again = propose(db_session, bond, tmp_path, column="venue_mic_code")
    [venue] = again.results
    assert (venue.outcome, venue.reason, venue.glossary_run_id) == ("no_element_back_to_05",
                                                                    "every_candidate_rejected", run.id)
    assert GlossaryRunRepository(db_session).get(run.id).columns == ["rating", "maturity", "venue_mic_code"]


def test_a_column_the_glossary_builder_was_asked_about_is_not_sent_again(db_session, bond, tmp_path):
    from tests.configured import glossary_proposal, new_element

    glossary_proposal(db_session, [new_element("rating")], feed_id=bond.id, column="rating", decision="rejected")
    proposed = propose(db_session, bond, tmp_path)
    [rating] = [r for r in proposed.results if r.source_field == "rating"]
    assert (rating.outcome, rating.glossary_run_id) == ("no_element_back_to_05", None)
    assert GlossaryRunRepository(db_session).active(bond.id).columns == ["maturity"]


# ---------------------------------------------------------------- the input (note 2)


def test_only_a_registered_feed_is_mapped(db_session, glossary):
    candidate = FeedRepository(db_session).create_candidate(code="syn_candidate", name="C")
    with pytest.raises(FeedNotRegistered):
        mapping_input(db_session, feed_id=candidate.id)


def test_the_input_is_the_latest_profile_and_the_hint_is_one_candidate(db_session, bond):
    agent_input = mapping_input(db_session, feed_id=bond.id)
    columns = {c.name: c for c in agent_input.columns}
    assert list(columns) == [c.name for c in BOND_COLUMNS]
    hinted = [(c.key, c.from_hint) for c in columns["instr_id"].candidates if c.from_hint]
    assert hinted == [("instrument_id", True)] and len(columns["instr_id"].candidates) > 1
    assert all(len(c.candidates) <= 10 for c in agent_input.columns)


def test_an_approved_mapping_wins_over_the_specs_hint(db_session, bond, tmp_path):
    answer = {"answers": [{"column": "instr_id", "elements": ["instrument_id", "isin"], "reason": "r"}]}
    propose(db_session, bond, tmp_path, column="instr_id", provider=FixedProvider(answer))
    decide(db_session, pending(db_session, bond, "instr_id"), choice="isin")
    [mapping] = MappingRepository(db_session).current_for_feed(bond.id)
    assert (mapping.source_field, mapping.element_key) == ("instr_id", "isin")  # the spec says instrument_id
    [again] = mapping_input(db_session, feed_id=bond.id, column="instr_id").columns
    assert again.current == "isin"


def test_nothing_to_map_and_a_column_already_proposed_are_409(db_session, bond, tmp_path):
    propose(db_session, bond, tmp_path, column="cpn")
    with pytest.raises(MappingAlreadyProposed):
        mapping_input(db_session, feed_id=bond.id, column="cpn")
    with pytest.raises(NothingToMap):
        mapping_input(db_session, feed_id=bond.id, column="no_such_column")


def test_a_proposal_during_the_call_is_409_and_nothing_is_written(db_session, bond, tmp_path):
    from core.repositories.tables import ProposalLogDiscardRow
    from tests.configured import mapping_proposal

    agent_input = mapping_input(db_session, feed_id=bond.id, column="cpn")
    end_read(db_session)

    def meanwhile(data):  # another steward's proposal for the same column is written during the call
        mapping_proposal(db_session, bond.id, "cpn", ["coupon_rate"])
        return {"answers": [{"column": "cpn", "elements": ["coupon_rate"], "reason": "r"}]}

    with pytest.raises(MappingAlreadyProposed):
        record_mappings(db_session, agent=agent(tmp_path, FixedProvider(meanwhile)), agent_input=agent_input, user=SAM)
    assert len(ProposalRepository(db_session).list_for_feed_column("mapping", bond.id, "cpn")) == 1
    assert db_session.query(ProposalLogDiscardRow).count() == 1


@pytest.mark.parametrize("meanwhile", ["mapped", "rejected"])
def test_a_decision_during_the_call_is_409_and_nothing_is_written(db_session, bond, tmp_path, meanwhile):
    """Review finding 5: while the agent answers, another proposal for the column is made and decided, so no
    proposal is pending when the answer comes. The column's mapping, or its rejected elements, differ from
    the input: MappingChangedMeanwhile, a discard row, no second proposal."""
    from core.repositories.errors import MappingChangedMeanwhile
    from core.repositories.tables import ProposalLogDiscardRow
    from tests.configured import approved_mapping, mapping_proposal

    agent_input = mapping_input(db_session, feed_id=bond.id, column="cpn")
    end_read(db_session)

    def decided_meanwhile(data):
        if meanwhile == "mapped":
            approved_mapping(db_session, bond.id, "cpn", "coupon_rate")
        else:
            mapping_proposal(db_session, bond.id, "cpn", ["coupon_rate"], decision="rejected", note="Not the coupon.")
        return {"answers": [{"column": "cpn", "elements": ["coupon_rate"], "reason": "r"}]}

    with pytest.raises(MappingChangedMeanwhile, match="cpn"):
        record_mappings(db_session, agent=agent(tmp_path, FixedProvider(decided_meanwhile)), agent_input=agent_input,
                        user=SAM)
    assert len(ProposalRepository(db_session).list_for_feed_column("mapping", bond.id, "cpn")) == 1
    assert db_session.query(ProposalLogDiscardRow).count() == 1


# ---------------------------------------------------------------- the steward's decision (assumption 28)


def test_a_tie_needs_a_choice(db_session, bond, tmp_path):
    propose(db_session, bond, tmp_path, column="date")
    with pytest.raises(ChoiceNeeded):
        decide(db_session, pending(db_session, bond, "date"))
    assert decide(db_session, pending(db_session, bond, "date"), choice="price_date").mapping.element_key == "price_date"


def test_a_choice_that_was_not_offered_is_refused(db_session, bond, tmp_path):
    propose(db_session, bond, tmp_path, column="date")
    with pytest.raises(ChoiceNotOffered):
        decide(db_session, pending(db_session, bond, "date"), choice="isin")


def test_an_element_another_column_holds_is_409(db_session, bond, tmp_path):
    propose(db_session, bond, tmp_path, column="matures_on")
    decide(db_session, pending(db_session, bond, "matures_on"))
    propose(db_session, bond, tmp_path, column="date")
    with pytest.raises(ElementAlreadyMapped):
        decide(db_session, pending(db_session, bond, "date"), choice="maturity_date")
    assert ProposalRepository(db_session).get(pending(db_session, bond, "date").id).status == "pending"


def test_propose_again_on_a_mapped_column_that_gets_no_element_queues_no_glossary_run(db_session, bond, tmp_path):
    """Review finding 8: the column has its mapping, so the glossary builder would skip it as mapped; no run
    is queued for it. The outcome is still no_element_back_to_05, with no run."""
    propose(db_session, bond, tmp_path, column="cpn")
    decide(db_session, pending(db_session, bond, "cpn"))
    nothing = FixedProvider({"answers": [{"column": "cpn", "elements": [], "reason": "No candidate is this column."}]})
    again = propose(db_session, bond, tmp_path, column="cpn", provider=nothing)
    [cpn] = again.results
    assert (cpn.outcome, cpn.source_field, cpn.glossary_run_id) == ("no_element_back_to_05", "cpn", None)
    assert GlossaryRunRepository(db_session).active(bond.id) is None
    assert MappingRepository(db_session).current_for_feed(bond.id)[0].element_key == "coupon_rate"


def test_propose_again_and_approve_gives_version_2(db_session, bond, tmp_path):
    propose(db_session, bond, tmp_path, column="date")
    decide(db_session, pending(db_session, bond, "date"), choice="price_date")
    propose(db_session, bond, tmp_path, column="date")
    second = decide(db_session, pending(db_session, bond, "date"), choice="issue_date")
    assert second.mapping.version == 2
    assert [(m.version, m.element_key) for m in MappingRepository(db_session).history(bond.id, "date")] == [
        (1, "price_date"), (2, "issue_date")]


def test_a_proposal_that_would_change_nothing_is_not_created(db_session, bond, tmp_path):
    propose(db_session, bond, tmp_path, column="cpn")
    decide(db_session, pending(db_session, bond, "cpn"))
    again = propose(db_session, bond, tmp_path, column="cpn")
    assert (again.unchanged, again.results, again.recommended) == (["cpn"], [], [])
    assert pending(db_session, bond, "cpn") is None


def test_a_rejected_element_is_not_proposed_again_and_a_rejection_writes_no_mapping(db_session, bond, tmp_path):
    propose(db_session, bond, tmp_path, column="date")
    decide(db_session, pending(db_session, bond, "date"), decision="rejected", note="None of these.")
    assert MappingRepository(db_session).current_for_feed(bond.id) == []
    [date] = mapping_input(db_session, feed_id=bond.id, column="date").columns
    assert not {"issue_date", "maturity_date", "price_date"} & {c.key for c in date.candidates}


def test_only_a_data_steward_decides_and_only_once(db_session, bond, tmp_path):
    propose(db_session, bond, tmp_path, column="cpn")
    proposal = pending(db_session, bond, "cpn")
    with pytest.raises(NotAnApprover):
        decide(db_session, proposal, user=GLENN)
    decide(db_session, proposal)
    with pytest.raises(ProposalAlreadyDecided):
        decide(db_session, proposal)
