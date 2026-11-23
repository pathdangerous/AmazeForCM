"""LoadRepository: one delivery of a registered feed (steps 12-15), its stages and standard records, and the
go-lives that approve loading (phase 6, migration 0018). Phase 7 adds statuses such as quarantined by migration."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.domain.load import STAGES, LoadStageDraft, StandardRecordDraft
from core.repositories.errors import (
    FeedNotFound,
    FeedNotRegistered,
    LoadInProgress,
    LoadNotFound,
    ProposalForAnotherFeed,
    ProposalNotApproved,
    WrongProposalKind,
)
from core.repositories.certifications import LoadCertificationRepository
from core.repositories.feeds import FeedRepository
from core.repositories.go_lives import GoLiveRepository
from core.repositories.loads import LoadRepository
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.standard_records import StandardRecordRepository
from tests.configured import a_load, go_live, live_feed, logged_call
from tests.conftest import dev_user_for

FILE = "local://raw/synthetic_prices/2026-01-02.csv"
TEST = {"purpose": "go_live_test"}


@pytest.fixture
def feeds(db_session):
    return FeedRepository(db_session)


@pytest.fixture
def registered(feeds, db_session):
    from tests.onboarded import registered_feed

    return registered_feed(db_session, feeds.create_candidate(code="synthetic_prices", name="Synthetic prices").id)


@pytest.fixture
def repo(db_session):
    return LoadRepository(db_session)


def stage(name, outcome="stage_completed", rows=1):
    now = datetime.now(UTC)
    return LoadStageDraft(stage=name, outcome=outcome, rows_in=rows, rows_out=rows, detail={"n": rows},
                          started_at=now, finished_at=now)


def test_create_on_a_registered_feed(repo, registered):
    load = repo.create(feed_id=registered.id, file_ref=FILE, **TEST)
    assert (load.status, load.purpose, load.fetch_id, load.go_live_id) == ("received", "go_live_test", None, None)
    assert load.received_at is not None and load.finished_at is None
    assert repo.get(load.id) == load


def test_only_registered_feeds_receive_loads(repo, feeds):
    candidate = feeds.create_candidate(code="not_yet", name="Not yet")
    with pytest.raises(FeedNotRegistered):
        repo.create(feed_id=candidate.id, file_ref="local://raw/x.csv", **TEST)
    with pytest.raises(FeedNotFound):
        repo.create(feed_id=uuid.uuid4(), file_ref="local://raw/x.csv", **TEST)


def test_a_feed_has_one_received_load_at_a_time(repo, registered):
    """G9 (uq_loads_one_received_per_feed): the second is LoadInProgress; once the first has finished, another
    may come."""
    repo.create(feed_id=registered.id, file_ref=FILE, **TEST)
    with pytest.raises(LoadInProgress):
        repo.create(feed_id=registered.id, file_ref=FILE, **TEST)


def test_list_for_feed_newest_first(db_session, repo, registered):
    ids = [a_load(db_session, registered.id, "loaded").id for _ in range(3)]
    assert [load.id for load in repo.list_for_feed(registered.id)] == list(reversed(ids))


def test_a_scheduled_load_names_a_go_live_and_a_test_load_has_none(repo, registered):
    for fields in ({"purpose": "scheduled"}, {"purpose": "go_live_test", "go_live_id": uuid.uuid4()}):
        with pytest.raises(ValidationError, match="go-live"):
            repo.create(feed_id=registered.id, file_ref=FILE, **fields)


def test_the_stages_are_recorded_in_order_and_the_load_finishes_loaded(db_session, repo, registered):
    load = repo.create(feed_id=registered.id, file_ref=FILE, **TEST)
    repo.record_stages(load.id, [stage(name, rows=3) for name in STAGES])
    assert [(s.stage, s.outcome, s.rows_in, s.detail) for s in repo.stages(load.id)] == [
        (name, "stage_completed", 3, {"n": 3}) for name in STAGES]
    finished = repo.finish(load.id, status="loaded")
    assert finished.status == "loaded" and finished.finished_at is not None


def test_a_failed_load_keeps_its_failed_stage_and_reason(db_session, repo, registered):
    load = repo.create(feed_id=registered.id, file_ref=FILE, **TEST)
    repo.record_stages(load.id, [stage("raw"), stage("curated", "failed_rolled_back")])
    failed = repo.finish(load.id, status="failed_rolled_back", failed_stage="curated", failure="3 values do not cast")
    assert (failed.status, failed.failed_stage, failed.failure) == ("failed_rolled_back", "curated",
                                                                    "3 values do not cast")
    assert [(s.stage, s.outcome) for s in repo.stages(load.id)] == [("raw", "stage_completed"),
                                                                     ("curated", "failed_rolled_back")]


def test_finishing_without_the_proof_is_refused_by_the_database(db_session, repo, registered):
    load = repo.create(feed_id=registered.id, file_ref=FILE, **TEST)
    repo.record_stages(load.id, [stage("raw")])
    with pytest.raises(DBAPIError, match="cannot be loaded"):
        with db_session.begin_nested():
            repo.finish(load.id, status="loaded")


@pytest.mark.parametrize("status", ["held", "released"])
def test_a_certified_scheduled_load_is_held_or_released(db_session, repo, registered, status):
    """From 0019 (G13) the only way to held or released: a scheduled load with a certification of that outcome."""
    load = a_load(db_session, registered.id, status)
    assert (load.status, load.purpose) == (status, "scheduled")
    assert LoadCertificationRepository(db_session).for_load(load.id).outcome == (
        "released" if status == "released" else "held_consumers_warned")


@pytest.mark.parametrize("status", ["held", "released"])
def test_a_test_load_is_never_held_or_released(db_session, repo, registered, status):
    load = a_load(db_session, registered.id, "loaded")
    with pytest.raises(DBAPIError, match="only a scheduled load is released or held"):
        with db_session.begin_nested():
            repo.set_status(load.id, status)


def test_a_received_load_cannot_be_released(db_session, repo, registered):
    load = repo.create(feed_id=registered.id, file_ref=FILE, **TEST)
    with pytest.raises(DBAPIError, match="moves only to loaded, failed_rolled_back, quarantined, held or abandoned"):
        with db_session.begin_nested():
            repo.set_status(load.id, "released")


def test_invalid_status_and_unknown_load(repo, registered):
    load = repo.create(feed_id=registered.id, file_ref=FILE, **TEST)
    with pytest.raises(ValidationError):
        repo.set_status(load.id, "lost")
    with pytest.raises(LoadNotFound):
        repo.get(uuid.uuid4())


# ---------------------------------------------------------------- phase 7: arrival, abandon, history, certification

SHA_A, SHA_B = "a" * 64, "b" * 64


@pytest.fixture
def live(db_session, repo):
    """(feed id, go-live id): a registered feed with a go-live, which a scheduled load names."""
    feed = live_feed(db_session, "syn_repo_arrival")
    return feed, GoLiveRepository(db_session).latest(feed).id


def scheduled(repo, live):
    return repo.create(feed_id=live[0], file_ref=FILE, purpose="scheduled", go_live_id=live[1])


def test_an_arrival_is_recorded_once(db_session, repo, live):
    load = scheduled(repo, live)
    arrived = repo.record_arrival(load.id, outcome="on_time_complete", content_sha256=SHA_A, detail={"rows": 3})
    assert (arrived.status, arrived.arrival_outcome, arrived.content_sha256, arrived.arrival_detail) == (
        "received", "on_time_complete", SHA_A, {"rows": 3})
    with pytest.raises(DBAPIError, match="does not change once set"):
        with db_session.begin_nested():
            repo.record_arrival(load.id, outcome="on_time_complete", content_sha256=SHA_B, detail={})


@pytest.mark.parametrize(("outcome", "status"), [("resent_quarantined", "quarantined"), ("breaking_held", "held")])
def test_a_resent_or_breaking_file_ends_the_load_on_arrival(repo, live, outcome, status):
    load = repo.record_arrival(scheduled(repo, live).id, outcome=outcome, content_sha256=SHA_A, detail={})
    assert (load.status, load.finished_at is not None, repo.stages(load.id)) == (status, True, [])


def test_an_additive_arrival_names_the_profile_of_its_file(db_session, repo, live):
    profile = FeedProfileRepository(db_session).latest(live[0])
    load = repo.record_arrival(scheduled(repo, live).id, outcome="additive_parked", content_sha256=SHA_A,
                               detail={"added": ["new_column"]}, profile_id=profile.id)
    assert (load.status, load.arrival_profile_id) == ("received", profile.id)


def test_a_stale_load_is_abandoned_with_its_failure(repo, live):
    abandoned = repo.abandon(scheduled(repo, live).id, "Left received by a process that is gone.")
    assert (abandoned.status, abandoned.failure, abandoned.finished_at is not None) == (
        "abandoned", "Left received by a process that is gone.", True)
    assert scheduled(repo, live).status == "received"  # the feed may load again


def test_scheduled_hashes_are_those_of_loaded_released_or_held_loads_only(repo, live):
    """Phase 7 assumption 12: a quarantined, abandoned or failed load's file is not compared with later ones."""
    first = repo.record_arrival(scheduled(repo, live).id, outcome="breaking_held", content_sha256=SHA_A, detail={})
    resent = repo.record_arrival(scheduled(repo, live).id, outcome="resent_quarantined", content_sha256=SHA_A, detail={})
    gone = repo.record_arrival(scheduled(repo, live).id, outcome="on_time_complete", content_sha256=SHA_B, detail={})
    repo.abandon(gone.id, "Synthetic.")
    assert (first.status, resent.status) == ("held", "quarantined")
    assert repo.scheduled_hashes(live[0]) == {SHA_A: first.id}  # SHA_B: abandoned; the quarantined copy: not the oldest
    assert repo.scheduled_hashes(uuid.uuid4()) == {}


def test_fully_loaded_scheduled_are_the_newest_that_completed_four_stages(db_session, repo, live):
    released = a_load(db_session, live[0], "released")
    held = a_load(db_session, live[0], "held")
    repo.record_arrival(scheduled(repo, live).id, outcome="resent_quarantined", content_sha256=SHA_A, detail={})
    assert [load.id for load in repo.fully_loaded_scheduled(live[0], 10)] == [held.id, released.id]
    assert [load.id for load in repo.fully_loaded_scheduled(live[0], 1)] == [held.id]


def test_a_breaking_held_load_has_no_stage_and_is_not_in_the_history(db_session, repo, live):
    """Deviation 13: history is the loads that completed all four stages, whatever their status. A delivery held on
    arrival is `held` too, but never reached the engine."""
    released = a_load(db_session, live[0], "released")
    breaking = repo.record_arrival(scheduled(repo, live).id, outcome="breaking_held", content_sha256=SHA_A, detail={})
    assert breaking.status == "held" and repo.stages(breaking.id) == []
    assert [load.id for load in repo.fully_loaded_scheduled(live[0], 10)] == [released.id]


def test_a_certification_is_read_back_and_a_load_without_one_has_none(db_session, repo, registered):
    load = a_load(db_session, registered.id, "held")
    certifications = LoadCertificationRepository(db_session)
    certification = certifications.for_load(load.id)
    assert (certification.rows, certification.flagged_rows, str(certification.trust_score),
            str(certification.threshold), certification.warned) == (10, 5, "50.0", "90.0", [])
    assert certifications.for_load(uuid.uuid4()) is None
    with pytest.raises(DBAPIError, match="is not a loaded scheduled load"):  # held now: certified once
        with db_session.begin_nested():
            certifications.add(load_id=load.id, feed_id=registered.id, rows=10, flagged_rows=5, trust_score=50,
                               threshold=90, outcome="held_consumers_warned")
    with pytest.raises(LoadNotFound):
        repo.lock(uuid.uuid4())
    with pytest.raises(LoadNotFound):
        repo.record_stages(uuid.uuid4(), [stage("raw")])


def test_lock_returns_the_load(repo, registered):
    load = repo.create(feed_id=registered.id, file_ref=FILE, **TEST)
    assert repo.lock(load.id) == load


def test_database_refuses_unknown_status(db_session, registered):
    # From 0018 trg_loads_a_status answers first (a new load is received); ck_loads_status is the second line
    with pytest.raises(DBAPIError, match="a new load is received, not lost"):
        db_session.execute(
            text("INSERT INTO loads (feed_id, file_ref, purpose, status) "
                 "VALUES (:f, 'local://raw/x.csv', 'go_live_test', 'lost')"),
            {"f": registered.id},
        )


# ---------------------------------------------------------------- standard records


def test_standard_records_are_added_counted_and_listed_by_row(db_session, repo, registered):
    records = StandardRecordRepository(db_session)
    load = repo.create(feed_id=registered.id, file_ref=FILE, **TEST)
    assert records.add_many(load_id=load.id, feed_id=registered.id, records=[
        StandardRecordDraft(row_number=2, values={"close_price": "1.50"}, flags=["px_positive"]),
        StandardRecordDraft(row_number=1, values={"close_price": "2.00"})]) == 2
    assert records.count(load.id) == 2 and records.count(uuid.uuid4()) == 0
    first, second = records.list_for_load(load.id)
    assert (first.row_number, first.values, first.flags) == (1, {"close_price": "2.00"}, [])
    assert (second.row_number, second.flags) == (2, ["px_positive"])
    assert [r.row_number for r in records.list_for_load(load.id, limit=1)] == [1]


def test_a_record_of_a_finished_load_is_refused_by_the_database(db_session, repo, registered):
    load = a_load(db_session, registered.id, "loaded")
    with pytest.raises(DBAPIError, match="written only while it is received"):
        with db_session.begin_nested():
            StandardRecordRepository(db_session).add_many(
                load_id=load.id, feed_id=registered.id, records=[StandardRecordDraft(row_number=1, values={})])


def test_a_standard_record_needs_a_row_number_from_one():
    with pytest.raises(ValidationError):
        StandardRecordDraft(row_number=0, values={})


# ---------------------------------------------------------------- go-lives


def test_a_go_live_pins_the_current_configuration_and_the_test_load(db_session, registered):
    from tests.configured import approved_rule

    rule = approved_rule(db_session, registered.id)
    live = go_live(db_session, registered.id)
    assert (live.version, live.approved_by, live.rule_ids, live.mapping_ids) == (1, "dev-data-owner", [rule.id], [])
    assert live.hypercare_until == date.today() + timedelta(days=10)
    assert GoLiveRepository(db_session).latest(registered.id) == live
    assert LoadRepository(db_session).get(live.test_load_id).purpose == "go_live_test"


def test_the_next_go_live_is_the_next_version_and_latest_is_the_newest(db_session, registered):
    first = go_live(db_session, registered.id)
    second = go_live(db_session, registered.id)
    assert (first.version, second.version) == (1, 2)
    assert GoLiveRepository(db_session).latest(registered.id).id == second.id
    assert GoLiveRepository(db_session).latest(uuid.uuid4()) is None


def test_a_go_live_needs_an_approved_review_summary_of_the_feed(db_session, registered, make_proposal):
    repo = GoLiveRepository(db_session)
    load = a_load(db_session, registered.id, "loaded")
    fields = {"feed_id": registered.id, "approved_by": "dev-data-owner", "mapping_ids": [], "rule_ids": [],
              "test_load_id": load.id, "hypercare_until": date.today(), "code_version_id": uuid.uuid4()}
    summary = make_proposal(kind="review_summary", roles=("data_owner",), feed_id=registered.id)
    with pytest.raises(ProposalNotApproved):
        repo.add_version(proposal_id=summary.id, **fields)
    with pytest.raises(WrongProposalKind):
        from core.repositories.proposals import ProposalRepository

        mapping = make_proposal(kind="mapping", feed_id=registered.id)
        ProposalRepository(db_session).record_decision(mapping.id, user=dev_user_for("data_steward"),
                                                       decision="approved")
        repo.add_version(proposal_id=mapping.id, **fields)
    with pytest.raises(FeedNotFound):
        repo.add_version(proposal_id=summary.id, **{**fields, "feed_id": uuid.uuid4()})


def test_a_go_live_from_another_feeds_summary_is_refused(db_session, registered, feeds):
    from core.repositories.proposals import ProposalRepository
    from tests.onboarded import registered_feed

    other = registered_feed(db_session, feeds.create_candidate(code="syn_other_feed", name="Other").id)
    proposal = ProposalRepository(db_session).create(
        proposal_log_id=logged_call(db_session, "ReviewSummariserAgent", "review_summariser").id,
        kind="review_summary", payload={}, autonomy="A1", required_approver_roles=["data_owner"], feed_id=other.id)
    ProposalRepository(db_session).record_decision(proposal.id, user=dev_user_for("data_owner"), decision="approved")
    with pytest.raises(ProposalForAnotherFeed):
        GoLiveRepository(db_session).add_version(
            feed_id=registered.id, proposal_id=proposal.id, approved_by="dev-data-owner", mapping_ids=[],
            rule_ids=[], test_load_id=a_load(db_session, registered.id, "loaded").id, hypercare_until=date.today(),
            code_version_id=uuid.uuid4())  # refused for the proposal before the pin is looked at
