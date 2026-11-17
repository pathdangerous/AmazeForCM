"""Step 11, go-live approval (phase 6 plan, assumptions 21 to 27; sections 5.1 to 5.3; phase 4 note 4).

Outcomes (CLAUDE.md): approved_scheduled, rejected_back_to_step, test_failed_held. Prepare: the checks, a test load
through the engine, then the ReviewSummariserAgent's summary; the feed's registered owner decides.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from agents.review_summariser import ReviewSummariserAgent
from core.domain.profiling import FileColumn
from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.errors import (
    ConfigurationChangedMeanwhile,
    ConfigurationChangedSinceSummary,
    FeedHasNoFile,
    FeedNotRegistered,
    GoLiveAlreadyProposed,
    NotAnApprover,
    NotTheFeedsOwner,
    NothingToGoLive,
    OwnerNotADataOwner,
    ProposalAlreadyDecided,
)
from core.repositories.feeds import FeedRepository
from core.repositories.go_lives import GoLiveRepository
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.tables import ProposalLogEntry
from core.steps.errors import BackToStepNeeded, NoteNeeded
from core.steps.s11_go_live import (
    ApprovedScheduled,
    GoLiveSummary,
    RejectedBackToStep,
    TestFailedHeld,
    decide_go_live,
)
from ports.identity.base import User
from ports.llm.mock import MockProvider
from ports.notify.inapp import InAppNotifier, NotificationRow
from ports.storage.local import LocalBlobStore
from tests.configured import a_registered_feed, approved_mapping, approved_rule, glossary_proposal, new_element
from tests.conftest import dev_user_for
from tests.step_chains import prepare_go_live

KEY = "raw/test/syn_go_live/f.csv"
GOOD = "instr_id,trade_date,traded_qty,note\nsyn-1,2026-11-02,120500,a\nsyn-2,2026-11-02,98200,b\n"
OWNER = "dev-data-owner"
TEAM, OPERATIONS = ["dev-backend-team"], ["dev-operations"]


@pytest.fixture
def world(db_session, glossary, tmp_path):
    from tests.onboarded import registered_feed
    from core.repositories.source_fetches import SourceFetchRepository

    feed = FeedRepository(db_session).create_candidate(code="syn_go_live", name="Go live")
    columns = [FileColumn(name=n, type="VARCHAR", null_count=0, null_rate=0.0, distinct_count=2)
               for n in ("instr_id", "trade_date", "traded_qty", "note")]
    registered_feed(db_session, feed.id, columns=columns)  # the profile the unmapped column is read from
    SourceFetchRepository(db_session).record(feed_id=feed.id, outcome="fetched", blob_ref=KEY, size_bytes=len(GOOD))
    for field, key in (("instr_id", "instrument_id"), ("trade_date", "price_date"), ("traded_qty", "volume")):
        approved_mapping(db_session, feed.id, field, key)
    blobs = LocalBlobStore(tmp_path / "blob")
    blobs.put(KEY, GOOD.encode())
    return SimpleNamespace(feed=feed.id, session=db_session, blobs=blobs, notifier=InAppNotifier(db_session))


def prepare(world, provider=None, **kw):
    agent = ReviewSummariserAgent(provider=provider or MockProvider(), blob_store=world.blobs)
    return prepare_go_live(world.session, blob_store=world.blobs, agent=agent, feed_id=world.feed,
                           notifier=world.notifier, backend_team=TEAM, owner_is_data_owner=lambda uid: True,
                           operations=OPERATIONS, stale_minutes=30, **kw)


def decide(world, summary, *, user=None, decision="approved", note=None, back_to_step=None, hypercare_days=10):
    return decide_go_live(world.session, proposal_id=summary.proposal_id, user=user or dev_user_for("data_owner"),
                          decision=decision, note=note, back_to_step=back_to_step, today=date(2026, 11, 17),
                          hypercare_days=hypercare_days, daily_run_utc="06:00", notifier=world.notifier,
                          backend_team=TEAM, operations=OPERATIONS, role_holders=lambda role: [f"dev-{role}"])


def told(world, recipient):
    return [n.subject for n in world.session.scalars(select(NotificationRow).where(NotificationRow.recipient == recipient))]


# ---------------------------------------------------------------- prepare


def test_prepare_clears_a_stale_received_load_first(world):
    """Phase 7 assumption 18: Prepare clears a load a dead process left received, then makes its own test load."""
    from datetime import UTC, datetime, timedelta

    repo = LoadRepository(world.session)
    old = repo.create(feed_id=world.feed, file_ref=KEY, purpose="go_live_test",
                      received_at=datetime.now(UTC) - timedelta(minutes=45))
    summary = prepare(world)
    assert isinstance(summary, GoLiveSummary) and summary.test_load.id != old.id
    assert repo.get(old.id).status == "abandoned"
    assert "Load abandoned: syn_go_live" in told(world, "dev-operations")


def test_prepare_with_a_younger_received_load_is_load_in_progress(world):
    from core.repositories.errors import LoadInProgress

    young = LoadRepository(world.session).create(feed_id=world.feed, file_ref=KEY, purpose="go_live_test")
    with pytest.raises(LoadInProgress):
        prepare(world)
    assert LoadRepository(world.session).get(young.id).status == "received"


def test_prepare_runs_a_test_load_then_drafts_the_summary(world):
    summary = prepare(world)
    assert isinstance(summary, GoLiveSummary) and summary.test_load.status == "loaded"
    proposal = ProposalRepository(world.session).get(summary.proposal_id)
    assert (proposal.kind, proposal.status, proposal.required_approver_roles) == ("review_summary", "pending", ["data_owner"])
    (load,) = LoadRepository(world.session).list_for_feed(world.feed)
    assert (load.purpose, load.status, load.go_live_id) == ("go_live_test", "loaded", None)
    assert proposal.payload["test_load_id"] == str(load.id)
    assert proposal.payload["mapping_ids"] == sorted(str(m.id) for m in MappingRepository(world.session)
                                                     .current_for_feed(world.feed))


def test_the_summary_lists_unmapped_columns_and_rejected_glossary_proposals(world):
    """Phase 5 note 4: the owner sees what is left out and what the glossary owner refused."""
    glossary_proposal(world.session, [new_element("maturity_date_x")], feed_id=world.feed, column="maturity",
                      decision="rejected")
    summary = prepare(world)
    payload = ProposalRepository(world.session).get(summary.proposal_id).payload
    assert payload["unmapped_columns"] == ["note"]
    assert [r["source_field"] for r in payload["rejected_glossary"]] == ["maturity"]
    assert "note" in payload["summary"] and "maturity" in payload["summary"]


def test_the_summary_lists_the_rules_with_their_back_test(world):
    approved_rule(world.session, world.feed, "volume_over_1m", sql='SELECT _row FROM curated WHERE "volume" > 1000000',
                  description="Volume is at most a million.")
    summary = prepare(world)
    payload = ProposalRepository(world.session).get(summary.proposal_id).payload
    assert payload["rule_ids"] == [str(r.id) for r in RuleRepository(world.session).current_for_feed(world.feed)]
    assert "volume_over_1m" in payload["summary"]


def test_a_failed_test_load_holds_the_feed_and_calls_no_model(world):
    """Assumption 22: no model call, no proposal; the backend team and the owner are told the stage and reason."""
    world.blobs.put(KEY, b"instr_id,trade_date,traded_qty\nsyn-1,2026-11-02,12x\n")
    provider = MockProvider()
    calls = world.session.query(ProposalLogEntry).count()
    held = prepare(world, provider)
    assert isinstance(held, TestFailedHeld) and held.outcome == "test_failed_held"
    assert (held.stage, held.reason) == ("curated", "values_do_not_cast")
    assert world.session.query(ProposalLogEntry).count() == calls  # no call row: no model call
    assert ProposalRepository(world.session).list_for_feed(world.feed, "review_summary") == []
    (load,) = LoadRepository(world.session).list_for_feed(world.feed)
    assert (load.status, load.id) == ("failed_rolled_back", held.load_id)
    assert told(world, "dev-backend-team") == ["Test load failed: syn_go_live (curated)"]
    assert told(world, OWNER) == ["Test load failed: syn_go_live (curated)"]


@pytest.mark.parametrize("setup", ["not registered", "no mapping", "pending summary", "no file", "not an owner"])
def test_prepare_is_refused_before_anything_is_written(db_session, glossary, tmp_path, setup, monkeypatch):
    """"no file" cannot happen to a feed that went through steps 02 to 04 (a profile needs a fetched file), so
    its fetch is taken away here; the check is the plan's all the same."""
    from core.repositories.source_fetches import SourceFetchRepository as Fetches

    if setup == "no file":
        monkeypatch.setattr(Fetches, "latest_fetched", lambda self, feed_id: None)
    from core.repositories.source_fetches import SourceFetchRepository
    from tests.onboarded import registered_feed

    candidate = FeedRepository(db_session).create_candidate(code=f"syn_refused_{setup.replace(' ', '_')}", name="X")
    if setup == "not registered":
        feed, error = candidate.id, FeedNotRegistered
    else:
        registered_feed(db_session, candidate.id)
        feed = candidate.id
        SourceFetchRepository(db_session).record(feed_id=feed, outcome="fetched", blob_ref=KEY, size_bytes=5)
        if setup != "no mapping":
            approved_mapping(db_session, feed, "instr_id", "instrument_id")
        error = {"no mapping": NothingToGoLive, "pending summary": GoLiveAlreadyProposed, "no file": FeedHasNoFile,
                 "not an owner": OwnerNotADataOwner}[setup]
        if setup == "pending summary":
            from tests.test_db_go_live_guards import summary_proposal
            from tests.configured import a_load

            load = a_load(db_session, feed, "loaded")
            summary_proposal(db_session, SimpleNamespace(feed=feed, mappings=[], rules=[], load=load.id),
                             decision=None)
    agent = ReviewSummariserAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path))
    loads = db_session.query(ProposalLogEntry).count()
    with pytest.raises(error):
        prepare_go_live(db_session, blob_store=LocalBlobStore(tmp_path), agent=agent, feed_id=feed,
                        notifier=InAppNotifier(db_session), backend_team=TEAM,
                        owner_is_data_owner=lambda uid: setup != "not an owner")
    assert all(load.status != "received" for load in LoadRepository(db_session).list_for_feed(feed))  # none begun
    assert db_session.query(ProposalLogEntry).count() == loads  # and no model call


def test_a_configuration_changed_during_the_call_is_a_discard(world):
    """Assumption 23: the pins are checked again under the feed lock after the call."""
    from tests.stand_in_agents import FixedProvider

    def during_the_call(data):
        approved_mapping(world.session, world.feed, "instr_id", "isin")  # a steward re-maps meanwhile
        world.session.commit()
        return {"summary": "Fine.", "highlights": [], "risks": []}

    with pytest.raises(ConfigurationChangedMeanwhile):
        prepare(world, FixedProvider(during_the_call))
    assert ProposalRepository(world.session).list_for_feed(world.feed, "review_summary") == []


# ---------------------------------------------------------------- the decision


def test_the_owners_approval_writes_the_go_live_and_sets_hypercare(world):
    summary = prepare(world)
    result = decide(world, summary)
    assert isinstance(result, ApprovedScheduled) and result.outcome == "approved_scheduled"
    assert result.hypercare_until == date(2026, 11, 27) and result.daily_run_utc == "06:00"
    live = GoLiveRepository(world.session).latest(world.feed)
    assert (live.version, live.approved_by, live.hypercare_until, live.proposal_id) == (
        1, OWNER, date(2026, 11, 27), summary.proposal_id)
    assert live.test_load_id == summary.test_load.id and result.go_live == live
    assert "Feed goes live: syn_go_live" in told(world, "dev-backend-team")
    assert "Feed goes live: syn_go_live" in told(world, "dev-operations")


def test_a_rejection_names_the_step_and_tells_its_role(world):
    summary = prepare(world)
    result = decide(world, summary, decision="rejected", note="Map the note column first.", back_to_step="06")
    assert isinstance(result, RejectedBackToStep) and result.outcome == "rejected_back_to_step"
    assert (result.back_to_step, result.note) == ("06", "Map the note column first.")
    assert ProposalRepository(world.session).get(summary.proposal_id).status == "rejected"
    assert GoLiveRepository(world.session).latest(world.feed) is None
    assert told(world, "dev-data_steward") == ["Sent back to step 06: syn_go_live"]


@pytest.mark.parametrize(("step", "role"), [("05", "glossary_owner"), ("06", "data_steward"), ("09", "data_steward")])
def test_each_step_a_rejection_goes_back_to_has_its_role(world, step, role):
    summary = prepare(world)
    decide(world, summary, decision="rejected", note="No.", back_to_step=step)
    assert told(world, f"dev-{role}") == [f"Sent back to step {step}: syn_go_live"]


def test_a_rejection_needs_a_note_and_a_step(world):
    summary = prepare(world)
    with pytest.raises(NoteNeeded):
        decide(world, summary, decision="rejected", note=" ", back_to_step="06")
    for step in (None, "07", "12"):
        with pytest.raises(BackToStepNeeded):
            decide(world, summary, decision="rejected", note="No.", back_to_step=step)
    assert ProposalRepository(world.session).get(summary.proposal_id).status == "pending"


def test_only_the_named_owner_decides(world):
    """Phase 4 note 4 / assumption 24: another data owner is 403 NotTheFeedsOwner."""
    summary = prepare(world)
    other = User(id="dev-another-owner", display_name="Another", email="a@example.test", roles=["data_owner"])
    with pytest.raises(NotTheFeedsOwner):
        decide(world, summary, user=other)
    with pytest.raises(NotAnApprover):
        decide(world, summary, user=dev_user_for("data_steward"))
    assert GoLiveRepository(world.session).latest(world.feed) is None


def test_an_owner_without_data_owner_is_409_at_prepare(world):
    agent = ReviewSummariserAgent(provider=MockProvider(), blob_store=world.blobs)
    with pytest.raises(OwnerNotADataOwner):
        prepare_go_live(world.session, blob_store=world.blobs, agent=agent, feed_id=world.feed,
                        notifier=world.notifier, backend_team=TEAM, owner_is_data_owner=lambda uid: False)
    assert told(world, "dev-backend-team") == ["Owner cannot approve go-live: syn_go_live"]


def test_a_summary_whose_owner_lost_the_role_is_403_at_the_decision(world):
    summary = prepare(world)
    former = User(id=OWNER, display_name="Devon", email="d@example.test", roles=["backend_team"])
    with pytest.raises(NotAnApprover):
        decide(world, summary, user=former)


def test_a_configuration_changed_since_the_summary_cannot_be_approved_but_can_be_rejected(world):
    summary = prepare(world)
    approved_rule(world.session, world.feed, "late_rule", sql="SELECT _row FROM curated", description="Late.")
    with pytest.raises(ConfigurationChangedSinceSummary):
        decide(world, summary)
    assert GoLiveRepository(world.session).latest(world.feed) is None
    assert isinstance(decide(world, summary, decision="rejected", note="Stale.", back_to_step="09"), RejectedBackToStep)


def test_a_decided_summary_is_not_decided_again(world):
    summary = prepare(world)
    decide(world, summary)
    with pytest.raises(ProposalAlreadyDecided):
        decide(world, summary)


def test_the_next_go_live_is_the_next_version(world):
    first = decide(world, prepare(world)).go_live
    second = decide(world, prepare(world)).go_live
    assert (first.version, second.version) == (1, 2)
    assert timedelta(days=10) == second.hypercare_until - date(2026, 11, 17)


def test_hypercare_days_come_from_the_caller(world):
    assert decide(world, prepare(world), hypercare_days=3).hypercare_until == date(2026, 11, 20)
