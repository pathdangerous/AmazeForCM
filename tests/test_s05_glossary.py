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


# ---------------------------------------------------------------- phase 7.8, task 6.1: the security master (D8) and units

UNIT_SEED = {"best_pe_ratio": "times", "best_px_bps_ratio": "times", "best_ev_to_best_ebitda": "times",
             "best_div_yld": "percent", "best_roe": "percent", "best_gross_margin": "percent",
             "best_eps_4_week_percent_change": "percent", "best_eps_6_month_percent_change": "percent",
             "best_eps_3_month_percent_change": "percent", "best_eps_1_week_percent_change": "percent"}


def d8_registered(db_session, tmp_path, code="syn_security_master"):
    """The D8 file's real column facts (profiled from Synthetic_Security_Master.xlsx), as a registered feed."""
    from pathlib import Path

    from core.intake import canonical_csv, read_xlsx_bytes
    from core.steps.profiling import profile_csv

    demo = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic" / "Synthetic_Security_Master.xlsx"
    table, _ = read_xlsx_bytes(demo.read_bytes(), original_name=demo.name)
    csv = tmp_path / "master.csv"
    csv.write_bytes(canonical_csv(table))
    feed = FeedRepository(db_session).create_candidate(code=code, name="Security master")
    return registered_feed(db_session, feed.id, columns=profile_csv(csv).columns,
                           spec=SourceSpec(source_code=code, elements=[], other_fields=[], columns={}))


@pytest.fixture
def master(db_session, glossary, tmp_path):
    return d8_registered(db_session, tmp_path)


def test_the_units_seed_is_exactly_what_the_builder_cannot_know():
    import json

    from ports.config import REPO_ROOT

    seed = json.loads((REPO_ROOT / "fixtures" / "glossary" / "units.json").read_text(encoding="utf-8"))
    assert set(seed) == {"description", "units"} and seed["units"] == UNIT_SEED


def test_the_d8_run_proposes_every_one_of_the_thirty_columns(db_session, master, tmp_path, notifier):
    recorded = run(db_session, master, tmp_path, notifier)
    assert len(recorded.run.results) == 30 and len(recorded.results) == 30
    outcomes = {r.column: r.outcome for r in recorded.results}
    assert {c for c, o in outcomes.items() if o == "duplicate_alias"} == {"TICKER_NAME", "COUNTRY"}
    assert {o for c, o in outcomes.items() if c not in ("TICKER_NAME", "COUNTRY")} == {"proposed"}
    by_column = {r.column: r for r in recorded.results}
    assert by_column["TICKER_NAME"].element_key == "ticker" and by_column["COUNTRY"].element_key == "country_of_risk"
    assert len(NotificationRepository(db_session).list_for("dev-glossary-owner")) == 1


def test_the_d8_new_elements_have_names_definitions_types_and_the_estimates_domain(db_session, master, tmp_path,
                                                                                    notifier):
    run(db_session, master, tmp_path, notifier)
    elements = {}
    for column in ("SOURCE", "SHORTNAME", "SECTOR", "REGION", "F_PERIOD", "RECORD_DATE", "BEST_EPS", "BEST_SALES",
                   "BEST_EPS_1_WEEK_PERCENT_CHANGE", "BEST_PX_BPS_RATIO", "BEST_NET_GAAP"):
        [option] = pending(db_session, master, column).payload["options"]
        elements[column] = option["element"]
    assert elements["BEST_EPS"]["key"] == "best_eps" and elements["BEST_EPS"]["domain"] == "estimates"
    assert elements["BEST_EPS"]["name"] == "Best earnings per share estimate"
    assert "consensus estimate" in elements["BEST_EPS"]["definition"].lower()
    assert elements["BEST_NET_GAAP"]["name"] == "Best GAAP net income estimate"
    assert elements["BEST_PX_BPS_RATIO"]["name"] == "Best price to book ratio estimate"
    assert elements["BEST_EPS_1_WEEK_PERCENT_CHANGE"]["name"] == "Best earnings per share estimate, change over 1 week"
    assert elements["BEST_EPS_1_WEEK_PERCENT_CHANGE"]["data_type"] == "decimal"  # the file holds text placeholders
    assert [elements[c]["data_type"] for c in ("RECORD_DATE", "BEST_SALES", "SECTOR")] == ["date", "decimal", "string"]
    assert [(elements[c]["key"], elements[c]["domain"]) for c in ("SOURCE", "SHORTNAME", "SECTOR", "REGION",
                                                                 "F_PERIOD", "RECORD_DATE")] == [
        ("data_source", "security_master"), ("short_name", "security_master"), ("sector", "security_master"),
        ("region", "security_master"), ("fiscal_period", "security_master"), ("record_date", "security_master")]
    assert all(e["definition"].strip() and e["name"].strip() for e in elements.values())


def test_every_d8_proposal_can_be_approved_and_the_seeded_units_are_filled_and_marked(db_session, master, tmp_path,
                                                                                       notifier):
    recorded = run(db_session, master, tmp_path, notifier)
    seeded, written = {}, {}
    for result in recorded.results:
        proposal = ProposalRepository(db_session).get(result.proposal_id)
        if result.outcome == "proposed":
            option = proposal.payload["options"][0]["element"]
            seeded[option["key"]] = (option["unit"], proposal.payload["unit_source"])
        decided = decide(db_session, proposal, notifier=notifier)  # none is a PhraseTaken or refused by a guard
        written[decided.element.key] = decided.element.unit
    assert {k: u for k, (u, source) in seeded.items() if source == "seed"} == UNIT_SEED
    assert all(source == "seed" for k, (u, source) in seeded.items() if k in UNIT_SEED)
    assert all((u, source) == (None, None) for k, (u, source) in seeded.items() if k not in UNIT_SEED)
    assert {k: u for k, u in written.items() if u is not None} == UNIT_SEED  # the element carries the unit
    assert len(written) == 30  # 28 new elements and the two aliased ones (ticker, country_of_risk)


def test_the_log_keeps_what_the_model_said_and_a_model_unit_is_never_overwritten(db_session, glossary, tmp_path,
                                                                                  notifier):
    from sqlalchemy import text

    feed = FeedRepository(db_session).create_candidate(code="syn_units", name="Units")
    feed = registered_feed(db_session, feed.id, spec=SourceSpec(source_code="syn_units", elements=[], other_fields=[],
                                                                columns={}),
                           columns=[col("BEST_PE_RATIO", "DOUBLE"), col("BEST_ROE", "DOUBLE"), col("BEST_SALES", "DOUBLE")])

    def element(key, unit):
        return {"key": key, "name": key.replace("_", " ").capitalize(), "domain": "estimates", "data_type": "decimal",
                "definition": "Synthetic.", "aliases": [], "unit": unit}

    answer = {"answers": [{"column": "BEST_PE_RATIO", "reason": "r", "new_element": element("best_pe_ratio", "multiple")},
                          {"column": "BEST_ROE", "reason": "r", "new_element": element("best_roe", None)},
                          {"column": "BEST_SALES", "reason": "r", "new_element": element("best_sales", None)}]}
    recorded = run(db_session, feed, tmp_path, notifier, provider=FixedProvider(answer))
    payloads = {c: pending(db_session, feed, c).payload for c in ("BEST_PE_RATIO", "BEST_ROE", "BEST_SALES")}
    got = {c: (p["options"][0]["element"]["unit"], p["unit_source"]) for c, p in payloads.items()}
    assert got == {"BEST_PE_RATIO": ("multiple", "model"), "BEST_ROE": ("percent", "seed"),
                   "BEST_SALES": (None, None)}
    logged = db_session.execute(text("SELECT output FROM proposal_log WHERE id = :i"),
                                {"i": recorded.run.proposal_log_id}).scalar_one()
    assert {a["column"]: a["new_element"]["unit"] for a in logged["answers"]} == {
        "BEST_PE_RATIO": "multiple", "BEST_ROE": None, "BEST_SALES": None}  # the seed is not in what the model said


def test_an_alias_option_has_no_unit_source(db_session, master, tmp_path, notifier):
    run(db_session, master, tmp_path, notifier)
    assert pending(db_session, master, "TICKER_NAME").payload["unit_source"] is None


def test_an_edit_of_the_unit_is_accepted_and_the_approved_element_carries_it(db_session, master, tmp_path, notifier):
    run(db_session, master, tmp_path, notifier)
    old = pending(db_session, master, "BEST_SALES")
    option = {**old.payload["options"][0], "element": {**old.payload["options"][0]["element"], "unit": "USD"}}
    new = ProposalRepository(db_session).edit(old.id, payload={**old.payload, "options": [option]}, user=GLENN)
    decided = decide(db_session, new, notifier=notifier)
    assert (decided.element.key, decided.element.unit) == ("best_sales", "USD")
    assert GlossaryRepository(db_session).get_current("best_sales").unit == "USD"
