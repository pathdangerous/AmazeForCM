"""Step 12, arrival and structure (phase 7 plan, assumptions 5 to 17; sections 5.1 to 5.3).

Outcomes (CLAUDE.md): on_time_complete, late_alert (the daily job's, task 7), resent_quarantined, additive_parked,
breaking_held. Deterministic: no agent. The file is read only through BlobStore.get() and profiled by DuckDB with
no database connection held; the first match wins: resent, breaking, additive, on time.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from core.domain.load import LoadStageDraft
from core.domain.profiling import FileColumn, SourceSpec
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary_runs import GlossaryRunRepository
from core.repositories.loads import LoadRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.errors import LoadInProgress, LoadNotReceived, NotAScheduledLoad
from core.steps.s06_map import mapping_input
from core.steps.s12_arrival import (
    AdditiveParked,
    ArrivalResult,
    BreakingHeld,
    LateAlert,
    OnTimeComplete,
    Recipients,
    ResentQuarantined,
    arrival_plan,
    clear_stale_load,
)
from ports.notify.inapp import InAppNotifier, NotificationRow
from ports.storage.local import LocalBlobStore
from tests.configured import certified_load, go_live
from tests.onboarded import registered_feed
from tests.step_chains import arrive, end_read

HEADER = "instr_id,trade_date,px\n"
GOOD = HEADER + "syn-0001,2026-11-02,10.25\nsyn-0002,2026-11-02,11.5\n"
TEMPLATE = ("instr_id", "trade_date", "px")
SPEC = SourceSpec(source_code="syn_arrival", elements=["instrument_id", "price_date", "close_price"], other_fields=[],
                  columns={"instr_id": "instrument_id", "trade_date": "price_date", "px": "close_price"})
TEAM, OPERATIONS, OWNERS, STEWARDS = ["dev-backend-team"], ["dev-operations"], ["dev-glossary-owner"], ["dev-data-steward"]
RECIPIENTS = Recipients(glossary_owners=OWNERS, stewards=STEWARDS, backend_team=TEAM, operations=OPERATIONS)


def column(name):
    return FileColumn(name=name, type="VARCHAR", null_count=0, null_rate=0.0, distinct_count=2)


@pytest.fixture
def world(db_session, glossary, tmp_path):
    feed = FeedRepository(db_session).create_candidate(code="syn_arrival", name="Synthetic arrivals",
                                                       source_code="syn_arrival", connector="local_file",
                                                       source_ref="files/syn_arrival.csv")
    registered_feed(db_session, feed.id, spec=SPEC, columns=[column(n) for n in TEMPLATE])
    live = go_live(db_session, feed.id)
    return SimpleNamespace(feed=feed.id, live=live, session=db_session, blobs=LocalBlobStore(tmp_path / "blob"),
                           notifier=InAppNotifier(db_session), n=0)


def deliver(world, content: str | bytes):
    """A received scheduled load of a new file with this content (one received load per feed at a time)."""
    world.n += 1
    key = f"raw/syn_arrival/{world.n}/f.csv"
    world.blobs.put(key, content.encode() if isinstance(content, str) else content)
    return LoadRepository(world.session).create(feed_id=world.feed, file_ref=key, purpose="scheduled",
                                                 go_live_id=world.live.id)


def check(world, content: str, **kw):
    load = deliver(world, content)
    result = arrive(world.session, blob_store=world.blobs, load_id=load.id, notifier=world.notifier,
                    recipients=RECIPIENTS, **kw)
    return load, result, LoadRepository(world.session).get(load.id)


def told(world, subject_start: str) -> set[str]:
    rows = world.session.scalars(select(NotificationRow).where(NotificationRow.subject.like(f"{subject_start}%")))
    return {r.recipient for r in rows}


def a_consumer(world):
    """Riley's requirement, whose current choice selects the feed, onboarding."""
    requirements = RequirementRepository(world.session)
    requirement = requirements.create(requester_id="dev-requester", title="Synthetic consumer")
    feed = FeedRepository(world.session).get(world.feed)
    SourceChoiceRepository(world.session).record_known(requirement_id=requirement.id, feed_id=world.feed,
                                                       source_code=feed.source_code)
    requirements.advance(requirement.id, "source_selected")
    requirements.advance(requirement.id, "onboarding")
    return requirement


# ---------------------------------------------------------------- on_time_complete


def test_a_delivery_matching_its_template_goes_on_to_the_engine(world):
    load, result, after = check(world, GOOD)
    assert isinstance(result, OnTimeComplete) and (result.outcome, result.load_id) == ("on_time_complete", load.id)
    assert (after.status, after.arrival_outcome, after.arrival_profile_id) == ("received", "on_time_complete", None)
    assert after.content_sha256 == hashlib.sha256(GOOD.encode()).hexdigest()
    assert after.arrival_detail["columns"] == list(TEMPLATE)
    assert LoadRepository(world.session).stages(load.id) == []
    assert world.session.scalar(select(NotificationRow.id).limit(1)) is None  # nobody is told of a good delivery


def test_column_order_and_a_header_only_file_do_not_matter(world):
    """Assumption 9: the engine matches names, so order is free. Assumption 15: no data rows passes step 12."""
    _, shuffled, _ = check(world, "px,instr_id,trade_date\n10.25,syn-0001,2026-11-02\n")
    assert isinstance(shuffled, OnTimeComplete)
    LoadRepository(world.session).abandon(shuffled.load_id, "Synthetic.")
    _, empty, _ = check(world, HEADER)
    assert isinstance(empty, OnTimeComplete)


# ---------------------------------------------------------------- resent_quarantined


def earlier(world, content: str, how: str):
    """An earlier scheduled load of this file, ended the way `how` says."""
    if how in ("released", "held"):
        return certified_load(world.session, world.feed, how, content=content.encode())
    repo = LoadRepository(world.session)
    load = deliver(world, content)
    repo.record_arrival(load.id, outcome="on_time_complete", detail={},
                        content_sha256=hashlib.sha256(content.encode()).hexdigest())
    if how == "abandoned":
        return repo.abandon(load.id, "Synthetic: left received by a dead process.")
    now = datetime.now(UTC)
    repo.record_stages(load.id, [LoadStageDraft(stage="raw", outcome="failed_rolled_back", rows_in=1, rows_out=0,
                                                detail={}, started_at=now, finished_at=now)])
    return repo.finish(load.id, status="failed_rolled_back", failed_stage="raw", failure="Synthetic.")


@pytest.mark.parametrize("how", ["failed_rolled_back", "abandoned"])
def test_a_file_whose_earlier_load_never_loaded_is_not_a_resend(world, how):
    """Assumption 12 (deviation 7): after a new go-live, or a stale clear, the same file must load."""
    before = earlier(world, GOOD, how)
    assert before.status == how
    _, result, _ = check(world, GOOD)
    assert isinstance(result, OnTimeComplete)


@pytest.mark.parametrize("how", ["released", "held"])
def test_a_file_whose_earlier_load_was_released_or_held_is_still_a_resend(world, how):
    before = earlier(world, GOOD, how)
    _, result, _ = check(world, GOOD)
    assert isinstance(result, ResentQuarantined) and result.same_as == before.id


def test_a_byte_identical_delivery_is_quarantined_and_never_loaded(world):
    first = earlier(world, GOOD, "released")
    again, result, after = check(world, GOOD)
    assert isinstance(result, ResentQuarantined)
    assert (result.load_id, result.same_as) == (again.id, first.id)
    assert (after.status, after.arrival_outcome, after.finished_at is not None) == ("quarantined",
                                                                                    "resent_quarantined", True)
    assert LoadRepository(world.session).stages(again.id) == []
    assert told(world, "Delivery resent") == {*OPERATIONS, "dev-data-owner"}


def test_a_resent_file_is_quarantined_even_when_it_would_also_break(world):
    """First match wins: the SHA-256 is compared before the columns."""
    broken = HEADER.replace(",px", "") + "syn-0001,2026-11-02\n"
    first, held, _ = check(world, broken)  # held: it is no longer received, so the next delivery can come
    assert isinstance(held, BreakingHeld)
    _, result, _ = check(world, broken)
    assert isinstance(result, ResentQuarantined) and result.same_as == first.id


def test_a_changed_byte_is_a_new_delivery(world):
    first, _, _ = check(world, GOOD)
    LoadRepository(world.session).abandon(first.id, "Synthetic.")
    _, result, _ = check(world, GOOD.replace("11.5", "11.6"))
    assert isinstance(result, OnTimeComplete)


# ---------------------------------------------------------------- breaking_held


def test_a_missing_template_column_holds_the_delivery_and_warns_consumers(world):
    a_consumer(world)
    load, result, after = check(world, "instr_id,trade_date\nsyn-0001,2026-11-02\n")
    assert isinstance(result, BreakingHeld)
    assert (result.load_id, result.missing) == (load.id, ["px"]) and "px" in result.reason
    assert (after.status, after.arrival_outcome) == ("held", "breaking_held")
    assert after.arrival_detail["missing"] == ["px"] and LoadRepository(world.session).stages(load.id) == []
    assert told(world, "Delivery held") == {*TEAM, *OPERATIONS, "dev-data-owner", "dev-requester"}
    links = world.session.scalars(select(NotificationRow.link).where(NotificationRow.subject.like("Delivery held%")))
    assert set(links) == {f"#/feed/{world.feed}"}  # phase 7.5, P28 (plan, assumption 16)


def test_a_file_that_is_not_a_csv_table_is_held_too(world):
    _, result, after = check(world, HEADER.encode() + b"\xff\xfe\xfd,2026-11-02,1\n")  # not valid UTF-8
    assert isinstance(result, BreakingHeld) and result.missing == [] and "CSV table" in result.reason
    assert after.status == "held" and after.arrival_detail["missing"] == []
    assert after.content_sha256 is not None  # the bytes were read: only the table is unreadable


def test_a_file_missing_from_the_store_is_held(world):
    load = LoadRepository(world.session).create(feed_id=world.feed, file_ref="raw/syn_arrival/none/f.csv",
                                                purpose="scheduled", go_live_id=world.live.id)
    result = arrive(world.session, blob_store=world.blobs, load_id=load.id, notifier=world.notifier,
                    recipients=RECIPIENTS)
    assert isinstance(result, BreakingHeld) and "missing from the store" in result.reason
    assert LoadRepository(world.session).get(load.id).content_sha256 is None


# ---------------------------------------------------------------- additive_parked


def test_a_new_column_is_parked_profiled_and_sent_to_the_glossary_builder(world):
    load, result, after = check(world, "instr_id,trade_date,px,rating\nsyn-0001,2026-11-02,10.25,AA\n")
    assert isinstance(result, AdditiveParked) and result.added == ["rating"] and result.load_id == load.id
    assert (after.status, after.arrival_outcome, after.arrival_profile_id) == ("received", "additive_parked",
                                                                              result.profile_id)
    profiles = FeedProfileRepository(world.session)
    newest = profiles.latest(world.feed)
    assert newest.id == result.profile_id and newest.profiled_by == "step_12" and newest.coverage == []
    assert [c.name for c in newest.columns] == [*TEMPLATE, "rating"]
    assert newest.spec == SPEC and newest.row_count == 1 and newest.outcome == "profiled"
    run = GlossaryRunRepository(world.session).get(result.run_id)
    assert (run.reason, run.columns, run.profile_id, run.status) == ("structure_change", ["rating"], newest.id,
                                                                      "queued")
    assert told(world, "New column") == {*OWNERS, *STEWARDS, *TEAM}


def test_the_new_column_is_the_next_templates_so_it_is_parked_once(world):
    """Assumption 10: the template is the feed's latest profile, so the same extra column is not parked again."""
    first, parked, _ = check(world, "instr_id,trade_date,px,rating\nsyn-0001,2026-11-02,10.25,AA\n")
    LoadRepository(world.session).abandon(first.id, "Synthetic.")
    _, next_day, _ = check(world, "instr_id,trade_date,px,rating\nsyn-0001,2026-11-03,10.5,AA\n")
    assert isinstance(next_day, OnTimeComplete)


def test_an_active_glossary_run_is_reused_and_the_arrival_says_so(world):
    from core.steps.s05_glossary import queue_run

    queue_run(world.session, feed_id=world.feed, reason="requested", requested_by="dev-backend-team", columns=["x"])
    _, result, after = check(world, "instr_id,trade_date,px,rating\nsyn-0001,2026-11-02,10.25,AA\n")
    run = GlossaryRunRepository(world.session).get(result.run_id)
    assert run.reason == "requested" and run.columns == ["x", "rating"]  # the queued run takes the column
    assert after.arrival_detail["run_reused"] is True


def test_the_mapping_agent_sees_the_parked_column_on_the_load_profile(world, tmp_path):
    """Phase 5 note 6: the mapping step reads the feed's latest profile, which is now the load's own."""
    check(world, "instr_id,trade_date,px,rating\nsyn-0001,2026-11-02,10.25,AA\n")
    agent_input = mapping_input(world.session, feed_id=world.feed)
    assert [c.name for c in agent_input.columns] == ["instr_id", "trade_date", "px", "rating"]
    assert [c.name for c in mapping_input(world.session, feed_id=world.feed, column="rating").columns] == ["rating"]


def test_a_breaking_and_additive_file_is_breaking(world):
    _, result, _ = check(world, "instr_id,trade_date,rating\nsyn-0001,2026-11-02,AA\n")
    assert isinstance(result, BreakingHeld) and result.missing == ["px"]


# ---------------------------------------------------------------- a load left received by a dead process (assumption 18)


def stale_load(world, minutes: int, purpose="scheduled"):
    return LoadRepository(world.session).create(
        feed_id=world.feed, file_ref="raw/syn_arrival/old/f.csv", purpose=purpose,
        go_live_id=world.live.id if purpose == "scheduled" else None,
        received_at=datetime.now(UTC) - timedelta(minutes=minutes))


def test_a_received_load_older_than_the_limit_is_abandoned_and_operations_are_told(world):
    old = stale_load(world, 45)
    cleared = clear_stale_load(world.session, feed_id=world.feed, stale_minutes=30, notifier=world.notifier,
                               operations=OPERATIONS)
    assert cleared.id == old.id and cleared.status == "abandoned"
    assert cleared.finished_at is not None and "30 minutes" in cleared.failure
    assert LoadRepository(world.session).stages(old.id) == []
    assert told(world, "Load abandoned") == set(OPERATIONS)
    deliver(world, GOOD)  # the feed may load again


def test_a_younger_received_load_is_left_alone_and_a_new_one_is_load_in_progress(world):
    young = stale_load(world, 5, purpose="go_live_test")
    assert clear_stale_load(world.session, feed_id=world.feed, stale_minutes=30, notifier=world.notifier,
                            operations=OPERATIONS) is None
    assert LoadRepository(world.session).get(young.id).status == "received"
    with pytest.raises(LoadInProgress):
        deliver(world, GOOD)


def test_no_received_load_means_nothing_to_clear(world):
    assert clear_stale_load(world.session, feed_id=world.feed, stale_minutes=1, notifier=world.notifier,
                            operations=OPERATIONS) is None


# ---------------------------------------------------------------- refusals and shapes


def test_only_a_received_scheduled_load_is_checked(world):
    load = deliver(world, GOOD)
    LoadRepository(world.session).abandon(load.id, "Synthetic.")
    with pytest.raises(LoadNotReceived):
        arrival_plan(world.session, load_id=load.id)


def test_a_go_live_test_load_has_no_arrival(world):
    test_load = LoadRepository(world.session).create(feed_id=world.feed, file_ref="raw/syn_arrival/t/f.csv",
                                                     purpose="go_live_test")
    with pytest.raises(NotAScheduledLoad):
        arrival_plan(world.session, load_id=test_load.id)


def test_a_load_that_finished_meanwhile_writes_nothing(world):
    from core.steps.s12_arrival import check_arrival, record_arrival

    load = deliver(world, GOOD)
    plan = arrival_plan(world.session, load_id=load.id)
    end_read(world.session)
    checked = check_arrival(plan, blob_store=world.blobs)
    LoadRepository(world.session).abandon(load.id, "Synthetic: another clear got there first.")
    with pytest.raises(LoadNotReceived):
        record_arrival(world.session, checked, notifier=world.notifier, recipients=RECIPIENTS)
    assert LoadRepository(world.session).get(load.id).arrival_outcome is None


def test_the_results_are_typed_and_round_trip():
    from pydantic import TypeAdapter
    from uuid import uuid4

    adapter = TypeAdapter(ArrivalResult)
    load, other = uuid4(), uuid4()
    for value in (OnTimeComplete(load_id=load), LateAlert(feed_id=other, detail="Synthetic."),
                  ResentQuarantined(load_id=load, same_as=other),
                  AdditiveParked(load_id=load, added=["rating"], profile_id=other, run_id=load),
                  BreakingHeld(load_id=load, missing=["px"], reason="Synthetic.")):
        assert adapter.validate_python(adapter.dump_python(value)) == value
