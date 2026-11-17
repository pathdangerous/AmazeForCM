"""Step 05 glossary (core/steps/s05_glossary.py; phase 5 plan, assumptions 8 to 20, sections 5.1 to 5.4).

Outcomes (CLAUDE.md): proposed, duplicate_alias, conflict_to_owner, one per column that gets a proposal.
A run: queue, claim (a lease), read the input, end the transaction, the model call, then with the feed
and the run locked and checked again, the proposals, the results and done. The glossary owner decides.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from agents.glossary_builder import GlossaryBuilderAgent
from core.domain.profiling import FileColumn, SourceSpec
from core.repositories.errors import (
    FeedNotRegistered,
    NotAnApprover,
    PhraseTaken,
    ProposalAlreadyDecided,
    RunSuperseded,
)
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.glossary_runs import GlossaryRunRepository
from core.repositories.notifications import NotificationRepository
from core.repositories.proposals import ProposalRepository
from core.steps.errors import ChoiceNeeded, ChoiceNotOffered
from core.steps.s05_glossary import (
    ConflictToOwner,
    DuplicateAlias,
    Proposed,
    builder_input,
    claim_run,
    decide_element,
    fail_run,
    queue_run,
    record_run,
)
from ports.llm.mock import MockProvider
from ports.notify.inapp import InAppNotifier
from ports.storage.local import LocalBlobStore
from tests.conftest import dev_user_for
from tests.onboarded import registered_feed
from tests.stand_in_agents import FixedProvider
from tests.step_chains import end_read, run_glossary

GLENN = dev_user_for("glossary_owner")
SAM = dev_user_for("data_steward")
OWNERS = ["dev-glossary-owner"]
STEWARDS = ["dev-data-steward"]


def col(name, type_):
    return FileColumn(name=name, type=type_, null_count=0, null_rate=0.0, distinct_count=10)


BOND_SPEC = SourceSpec(source_code="syn_bond_terms", elements=["instrument_id", "maturity_date", "coupon_rate"],
                       other_fields=[], columns={"instr_id": "instrument_id", "matures_on": "maturity_date",
                                                 "cpn": "coupon_rate"})
BOND_COLUMNS = [col("instr_id", "VARCHAR"), col("matures_on", "DATE"), col("cpn", "DOUBLE"), col("date", "DATE"),
                col("venue_mic_code", "VARCHAR"), col("rating", "VARCHAR"), col("maturity", "VARCHAR")]


@pytest.fixture
def bond(db_session, glossary):
    feed = FeedRepository(db_session).create_candidate(code="syn_bond_terms", name="Bond terms")
    return registered_feed(db_session, feed.id, spec=BOND_SPEC, columns=BOND_COLUMNS)


@pytest.fixture
def notifier(db_session):
    return InAppNotifier(db_session)


def builder(tmp_path, provider=None):
    return GlossaryBuilderAgent(provider=provider or MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))


def run(db_session, feed, tmp_path, notifier, *, provider=None, reason="registered", columns=()):
    queue_run(db_session, feed_id=feed.id, reason=reason, requested_by="dev-backend-team", columns=columns)
    end_read(db_session)
    return run_glossary(db_session, agent=builder(tmp_path, provider), notifier=notifier, owners=OWNERS)


def pending(db_session, feed, column):
    return ProposalRepository(db_session).pending_for_column("glossary_element", feed.id, column)


def test_a_structure_change_run_asks_only_about_its_own_columns(db_session, bond, tmp_path, notifier):
    """Phase 7 assumption 13: a structure_change run looks at the columns it names (the parked ones) and treats
    none as sent back, so the profile's other new columns are not asked about again."""
    queue_run(db_session, feed_id=bond.id, reason="structure_change", requested_by="step_12", columns=["rating"])
    end_read(db_session)
    run_ = claim_run(db_session, lease_seconds=300)
    plan = builder_input(db_session, run_)
    assert [c.name for c in plan.agent_input.columns] == ["rating"]
    assert not plan.agent_input.columns[0].sent_back
    assert "venue_mic_code" not in {c.name for c in plan.agent_input.columns} and plan.skipped == {}


def test_a_structure_change_column_that_is_covered_is_skipped_not_asked(db_session, bond, tmp_path, notifier):
    queue_run(db_session, feed_id=bond.id, reason="structure_change", requested_by="step_12", columns=["cpn"])
    end_read(db_session)
    plan = builder_input(db_session, claim_run(db_session, lease_seconds=300))
    assert plan.agent_input is None  # cpn is declared as an element by the spec: not new, and not sent back


# ---------------------------------------------------------------- the three outcomes (section 5.4)


def test_proposed_when_no_element_has_the_columns_name(db_session, bond, tmp_path, notifier):
    recorded = run(db_session, bond, tmp_path, notifier)
    [rating] = [r for r in recorded.results if r.column == "rating"]
    assert isinstance(rating, Proposed) and rating.outcome == "proposed"
    proposal = ProposalRepository(db_session).get(rating.proposal_id)
    assert (proposal.status, proposal.required_approver_roles, proposal.feed_id) == ("pending", ["glossary_owner"],
                                                                                    bond.id)
    assert proposal.payload["options"][0]["element"]["key"] == "rating"


def test_duplicate_alias_when_the_new_element_is_an_existing_one_under_another_name(db_session, bond, tmp_path,
                                                                                     notifier):
    recorded = run(db_session, bond, tmp_path, notifier)
    [venue] = [r for r in recorded.results if r.column == "venue_mic_code"]
    assert isinstance(venue, DuplicateAlias) and (venue.outcome, venue.element_key) == ("duplicate_alias",
                                                                                        "exchange_mic")


def test_conflict_to_owner_when_the_name_is_taken_by_an_element_of_another_type_or_by_two(db_session, bond, tmp_path,
                                                                                         notifier):
    # another type: maturity, sent back by step 06 (text; maturity_date is a date)
    recorded = run(db_session, bond, tmp_path, notifier, reason="sent_back", columns=["maturity"])
    [maturity] = [r for r in recorded.results if r.column == "maturity"]
    assert isinstance(maturity, ConflictToOwner) and maturity.clashes == ["maturity_date"]
    assert ProposalRepository(db_session).get(maturity.proposal_id).payload["options"] == []
    # two elements: ccy, when another element also has the alias ccy; the agent proposes an element "ccy"
    GlossaryRepository(db_session).add_seed_element(key="trade_currency", name="Trade currency", domain="pricing",
                                                    data_type="string", definition="Synthetic.", aliases=["ccy"])
    other = FeedRepository(db_session).create_candidate(code="syn_ccy", name="Ccy")
    registered_feed(db_session, other.id, spec=SourceSpec(source_code="syn_ccy", elements=[], other_fields=[],
                                                          columns={}), columns=[col("ccy", "VARCHAR")])
    answer = {"answers": [{"column": "ccy", "reason": "r", "new_element": {
        "key": "ccy", "name": "Ccy", "domain": "pricing", "data_type": "string", "definition": "Synthetic."}}]}
    recorded = run(db_session, FeedRepository(db_session).get(other.id), tmp_path, notifier,
                   provider=FixedProvider(answer))
    [ccy] = recorded.results
    assert isinstance(ccy, ConflictToOwner) and ccy.clashes == ["currency", "trade_currency"]


# ---------------------------------------------------------------- what a run looks at (assumptions 8, 9, 12)


def test_a_run_looks_at_the_new_columns_and_skips_the_covered_ones(db_session, bond, tmp_path, notifier):
    recorded = run(db_session, bond, tmp_path, notifier)
    assert recorded.run.status == "done" and recorded.run.proposal_log_id is not None
    assert recorded.run.results == {"date": "left_to_step_06", "venue_mic_code": "duplicate_alias",
                                    "rating": "proposed", "maturity": "covered_by_name"}


def test_a_column_sent_back_skips_the_name_rule(db_session, bond, tmp_path, notifier):
    recorded = run(db_session, bond, tmp_path, notifier, reason="sent_back", columns=["maturity"])
    assert recorded.run.results["maturity"] == "conflict_to_owner"


def test_a_column_already_asked_is_not_asked_again(db_session, bond, tmp_path, notifier):
    run(db_session, bond, tmp_path, notifier)
    again = run(db_session, bond, tmp_path, notifier, reason="requested")
    assert again.run.results == {"date": "left_to_step_06", "venue_mic_code": "already_asked",
                                 "rating": "already_asked", "maturity": "covered_by_name"}
    assert [r.column for r in again.results] == []


def test_no_new_column_is_done_with_no_call_and_no_log_row(db_session, glossary, tmp_path, notifier):
    from core.repositories.tables import ProposalLogEntry

    feed = registered_feed(db_session, FeedRepository(db_session).create_candidate(code="syn_plain", name="P").id)
    provider = FixedProvider({"answers": []})
    before = db_session.scalar(select(func.count()).select_from(ProposalLogEntry))
    recorded = run(db_session, feed, tmp_path, notifier, provider=provider)
    assert (recorded.run.status, recorded.run.results, recorded.run.proposal_log_id, provider.calls) == (
        "done", {}, None, 0)
    assert db_session.scalar(select(func.count()).select_from(ProposalLogEntry)) == before


def test_the_input_is_built_from_the_runs_own_profile(db_session, bond):
    """Review finding 4: the profile the run names, not the feed's latest one (phase 7 profiles every load)."""
    from core.repositories.feed_profiles import FeedProfileRepository
    from tests.onboarded import profiled

    run, _ = queue_run(db_session, feed_id=bond.id, reason="registered", requested_by="dev-backend-team")
    later = profiled(db_session, bond.id, spec=BOND_SPEC, columns=[*BOND_COLUMNS, col("added_later", "VARCHAR")])
    assert FeedProfileRepository(db_session).latest(bond.id).id == later.id != run.profile_id
    plan = builder_input(db_session, claim_run(db_session, lease_seconds=60))
    assert [c.name for c in plan.agent_input.columns] == ["date", "venue_mic_code", "rating"]


def test_the_input_holds_candidates_only_and_no_values(db_session, bond):
    queue_run(db_session, feed_id=bond.id, reason="registered", requested_by="dev-backend-team")
    plan = builder_input(db_session, claim_run(db_session, lease_seconds=60))
    assert [c.name for c in plan.agent_input.columns] == ["date", "venue_mic_code", "rating"]
    assert all(len(c.candidates) <= 10 for c in plan.agent_input.columns)
    assert (plan.agent_input.source_domain, plan.skipped) == ("security_master", {"maturity": "covered_by_name"})


def test_the_glossary_owners_are_told_once_per_run_that_leaves_proposals(db_session, bond, tmp_path, notifier):
    run(db_session, bond, tmp_path, notifier)
    run(db_session, bond, tmp_path, notifier, reason="requested")  # leaves none
    told = NotificationRepository(db_session).list_for("dev-glossary-owner")
    assert [n.subject for n in told] == ["Glossary proposals wait: syn_bond_terms"]


# ---------------------------------------------------------------- queue, fail, a late answer


def test_only_a_registered_feed_gets_a_run(db_session, glossary):
    candidate = FeedRepository(db_session).create_candidate(code="syn_candidate", name="C")
    with pytest.raises(FeedNotRegistered):
        queue_run(db_session, feed_id=candidate.id, reason="requested", requested_by="dev-glossary-owner")


def test_asking_while_a_run_is_active_returns_it(db_session, bond):
    first, created = queue_run(db_session, feed_id=bond.id, reason="registered", requested_by="dev-backend-team")
    again, created_again = queue_run(db_session, feed_id=bond.id, reason="requested", requested_by="dev-glossary-owner")
    assert (again.id, created, created_again) == (first.id, True, False)


def test_a_failed_run_keeps_its_reason_and_writes_nothing_else(db_session, bond):
    queue_run(db_session, feed_id=bond.id, reason="registered", requested_by="dev-backend-team")
    claimed = claim_run(db_session, lease_seconds=60)
    failed = fail_run(db_session, run=claimed, error="LLMUnavailable: the model did not answer")
    assert (failed.status, failed.error) == ("failed", "LLMUnavailable: the model did not answer")
    assert ProposalRepository(db_session).list(kind="glossary_element") == []


def test_a_late_answer_after_the_lease_is_discarded_and_writes_nothing(db_session, bond, tmp_path, notifier):
    from core.repositories.tables import ProposalLogDiscardRow

    queue_run(db_session, feed_id=bond.id, reason="registered", requested_by="dev-backend-team")
    first = claim_run(db_session, lease_seconds=0)
    plan = builder_input(db_session, first)
    claim_run(db_session, lease_seconds=60)  # another worker takes it: attempt 2
    end_read(db_session)
    with pytest.raises(RunSuperseded):
        record_run(db_session, plan=plan, agent=builder(tmp_path), notifier=notifier, owners=OWNERS)
    assert ProposalRepository(db_session).list(kind="glossary_element") == []
    assert db_session.scalar(select(func.count()).select_from(ProposalLogDiscardRow)) == 1
    assert GlossaryRunRepository(db_session).get(first.id).attempt == 2


# ---------------------------------------------------------------- the glossary owner's decision (assumption 17)


@pytest.fixture
def proposals(db_session, bond, tmp_path, notifier):
    run(db_session, bond, tmp_path, notifier)
    return {c: pending(db_session, bond, c) for c in ("rating", "venue_mic_code")}


def decide(db_session, proposal, user=GLENN, decision="approved", choice=None, note=None, notifier=None):
    return decide_element(db_session, proposal_id=proposal.id, user=user, decision=decision, choice=choice, note=note,
                          notifier=notifier or InAppNotifier(db_session), stewards=STEWARDS)


def test_approving_a_new_element_stores_version_1(db_session, proposals, notifier):
    decided = decide(db_session, proposals["rating"], notifier=notifier)
    assert (decided.decision, decided.element.key, decided.element.version, decided.element.created_by) == (
        "approved", "rating", 1, "dev-glossary-owner")
    told = NotificationRepository(db_session).list_for("dev-data-steward")
    assert [n.subject for n in told] == ["Can now be mapped: rating of syn_bond_terms"]


def test_approving_an_alias_stores_the_next_version(db_session, proposals):
    before = GlossaryRepository(db_session).get_current("exchange_mic")
    decided = decide(db_session, proposals["venue_mic_code"])
    assert (decided.element.version, decided.element.aliases) == (before.version + 1,
                                                                  [*before.aliases, "venue_mic_code"])


def test_a_conflict_needs_a_choice_and_one_with_no_option_can_only_be_rejected(db_session, bond, tmp_path, notifier):
    recorded = run(db_session, bond, tmp_path, notifier, reason="sent_back", columns=["maturity"])
    [result] = [r for r in recorded.results if r.outcome == "conflict_to_owner"]
    conflict = ProposalRepository(db_session).get(result.proposal_id)
    with pytest.raises(ChoiceNeeded):
        decide(db_session, conflict)
    with pytest.raises(ChoiceNotOffered):
        decide(db_session, conflict, choice=0)
    rejected = decide(db_session, conflict, decision="rejected", note="Maturity is a tenor; not in phase 5.")
    assert (rejected.decision, rejected.element) == ("rejected", None)


def test_a_choice_that_was_not_offered_is_refused(db_session, proposals):
    with pytest.raises(ChoiceNotOffered):
        decide(db_session, proposals["rating"], choice=1)


def test_a_taken_phrase_is_409_and_nothing_is_written(db_session, proposals):
    GlossaryRepository(db_session).add_seed_element(key="credit_rating", name="Credit rating", domain="pricing",
                                                    data_type="string", definition="Synthetic.", aliases=["rating"])
    with pytest.raises(PhraseTaken):
        decide(db_session, proposals["rating"])
    assert ProposalRepository(db_session).get(proposals["rating"].id).status == "pending"
    assert not GlossaryRepository(db_session).exists("rating")


def test_a_rejection_writes_no_element_and_keeps_the_note(db_session, proposals):
    rejected = decide(db_session, proposals["rating"], decision="rejected", note="Not a standard element.")
    assert rejected.element is None and not GlossaryRepository(db_session).exists("rating")
    [approval] = ProposalRepository(db_session).approvals(proposals["rating"].id)
    assert (approval.decision, approval.note) == ("rejected", "Not a standard element.")


def test_only_a_glossary_owner_decides_and_only_once(db_session, proposals):
    with pytest.raises(NotAnApprover):
        decide(db_session, proposals["rating"], user=SAM)
    decide(db_session, proposals["rating"])
    with pytest.raises(ProposalAlreadyDecided):
        decide(db_session, proposals["rating"])
