"""Migration 0018's load guards (phase 6 plan, section 6: G7 to G11), by raw SQL.

The repositories are bypassed on purpose, so only the database decides. Each refusal goes through
answered_by(): the statement is refused with the guard's message, and with only that guard removed (inside a
savepoint rolled back at once) it is no longer refused with that message. G4 (append-only, no TRUNCATE) for
load_stages and standard_records is in test_append_only.py. Each test starts with a refusal, so against 0017
(no guard, and no new table) it fails.

The helpers below (new_load, add_stage, loaded_load, failed_load) are shared with test_db_go_live_guards.py.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from tests.configured import a_registered_feed
from tests.test_db_onboarding_guards import _branch_off, answered_by

STAGES = ("raw", "curated", "validated", "standard")

LOAD = ("INSERT INTO loads (feed_id, file_ref, purpose, go_live_id, status) "
        "VALUES (:feed, 'raw/test/f.csv', :purpose, :go_live, :status) RETURNING id")
STAGE = ("INSERT INTO load_stages (load_id, stage, outcome, rows_in, rows_out, detail, started_at, finished_at) "
         "VALUES (:load, :stage, :outcome, :rows_in, :rows_out, CAST(:detail AS jsonb), now(), now())")
RECORD = ('INSERT INTO standard_records (load_id, feed_id, row_number, "values", flags) '
          "VALUES (:load, :feed, :row, CAST(:vals AS jsonb), ARRAY[]::text[])")
MOVE = "UPDATE loads SET status = :status, finished_at = now() WHERE id = :id"


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def feed(s):
    return a_registered_feed(s, "syn_load_guards")


def load_params(feed, *, purpose="go_live_test", go_live=None, status="received"):
    return {"feed": feed, "purpose": purpose, "go_live": go_live, "status": status}


def new_load(s, feed, *, purpose="go_live_test", go_live=None) -> uuid.UUID:
    """A received load (the only status a load is created with)."""
    return s.execute(text(LOAD), load_params(feed, purpose=purpose, go_live=go_live)).scalar_one()


def stage_params(load, stage, outcome="stage_completed", **over):
    return {"load": load, "stage": stage, "outcome": outcome, "rows_in": 1, "rows_out": 1,
            "detail": json.dumps({}), **over}


def add_stage(s, load, stage, outcome="stage_completed") -> None:
    s.execute(text(STAGE), stage_params(load, stage, outcome))


def loaded_load(s, feed, *, purpose="go_live_test", go_live=None) -> uuid.UUID:
    """A load with its four stages completed, moved to loaded. A scheduled load first passes its arrival check
    (0019, G13: a scheduled load is loaded only after on_time_complete or additive_parked)."""
    load = new_load(s, feed, purpose=purpose, go_live=go_live)
    if purpose == "scheduled":
        s.execute(text("UPDATE loads SET content_sha256 = :sha, arrival_outcome = 'on_time_complete', "
                       "arrival_detail = '{}'::jsonb WHERE id = :id"), {"sha": uuid.uuid4().hex * 2, "id": load})
    for stage in STAGES:
        add_stage(s, load, stage)
    s.execute(text(MOVE), {"status": "loaded", "id": load})
    return load


def failed_load(s, feed, *, stage="raw") -> uuid.UUID:
    """A load whose first stages completed up to `stage`, which failed; moved to failed_rolled_back."""
    load = new_load(s, feed)
    for done in STAGES[:STAGES.index(stage)]:
        add_stage(s, load, done)
    add_stage(s, load, stage, "failed_rolled_back")
    s.execute(text(MOVE), {"status": "failed_rolled_back", "id": load})
    return load


def status_guard(message):
    return _branch_off("amaze_guard_load_status", message)


# ---------------------------------------------------------------- G7: the purpose fits the go-live

DROP_PURPOSE_FITS = "ALTER TABLE loads DROP CONSTRAINT ck_loads_purpose_fits"


@pytest.mark.parametrize(("purpose", "go_live"), [
    ("scheduled", None),             # the NULL case: a scheduled load names the go-live that approved it
    ("go_live_test", uuid.uuid4()),  # a test load has none (the check answers before the foreign key does)
    ("other", None), ("other", uuid.uuid4())])
def test_a_loads_purpose_fits_its_go_live(s, feed, purpose, go_live):
    answered_by(s, "ck_loads_purpose_fits", LOAD, load_params(feed, purpose=purpose, go_live=go_live),
                DROP_PURPOSE_FITS)
    new_load(s, feed)  # a test load with no go-live is accepted


# ---------------------------------------------------------------- G8: a load's status moves one way, with its proof


@pytest.mark.parametrize("status", ["loaded", "failed_rolled_back", "held", "released"])
def test_a_new_load_is_received(s, feed, status):
    answered_by(s, "a new load is received", LOAD, load_params(feed, status=status),
                status_guard("loads: a new load is received, not %"))
    new_load(s, feed)


def load_in(s, feed, status) -> uuid.UUID:
    """A load in this status, reached the way the guard allows: loaded or failed_rolled_back from received,
    held or released from a scheduled loaded load with a certification of that outcome (0019, G13)."""
    if status == "loaded":
        return loaded_load(s, feed)
    if status == "failed_rolled_back":
        return failed_load(s, feed)
    return certified_load_in(s, feed, status)


def certified_load_in(s, feed, status) -> uuid.UUID:
    """A scheduled load moved from loaded to `status` (held or released) under its certification."""
    from tests.configured import go_live
    from tests.test_db_arrival_guards import certified

    load = certified(s, (feed, go_live(s, feed).id), **({"flagged": 5} if status == "held" else {}))
    s.execute(text(MOVE), {"status": status, "id": load})
    return load


@pytest.mark.parametrize(("status", "to"), [
    ("loaded", "failed_rolled_back"), ("loaded", "received"),
    ("failed_rolled_back", "loaded"), ("failed_rolled_back", "received"), ("failed_rolled_back", "held"),
    ("held", "loaded"), ("held", "released"), ("released", "held"), ("released", "received")])
def test_a_finished_load_does_not_move(s, feed, status, to):
    load = load_in(s, feed, status)
    answered_by(s, "does not move", MOVE, {"status": to, "id": load},
                status_guard("loads: load % is %, and a finished load does not move"))


@pytest.mark.parametrize("to", ["held", "released"])
def test_a_loaded_load_may_be_held_or_released(s, feed, to):
    """Not a refusal: the one move after loaded that is allowed, for a scheduled load with a certification that
    says so (0019, G13; step 15). Without it no load could ever be released."""
    load = certified_load_in(s, feed, to)
    assert s.scalar(text("SELECT status FROM loads WHERE id = :id"), {"id": load}) == to


@pytest.mark.parametrize(("to", "match", "message"), [
    ("held", "can be held on arrival only as breaking_held",
     "loads: load % can be held on arrival only as breaking_held, not %"),
    ("released", "a received load moves only to loaded, failed_rolled_back, quarantined, held or abandoned",
     "loads: a received load moves only to loaded, failed_rolled_back, quarantined, held or abandoned, not %")])
def test_a_received_load_moves_only_to_loaded_or_failed(s, feed, to, match, message):
    load = new_load(s, feed)
    answered_by(s, match, MOVE, {"status": to, "id": load}, status_guard(message))


@pytest.mark.parametrize("completed", [0, 1, 2, 3])
def test_a_load_is_loaded_only_with_four_completed_stages(s, feed, completed):
    load = new_load(s, feed)
    for stage in STAGES[:completed]:
        add_stage(s, load, stage)
    answered_by(s, "cannot be loaded", MOVE, {"status": "loaded", "id": load},
                status_guard("loads: load % cannot be loaded: % of its four stages completed"))
    for stage in STAGES[completed:]:
        add_stage(s, load, stage)
    s.execute(text(MOVE), {"status": "loaded", "id": load})  # with all four it moves


def test_a_load_whose_stage_failed_is_not_loaded(s, feed):
    load = new_load(s, feed)
    add_stage(s, load, "raw")
    add_stage(s, load, "curated", "failed_rolled_back")
    answered_by(s, "cannot be loaded", MOVE, {"status": "loaded", "id": load},
                status_guard("loads: load % cannot be loaded: % of its four stages completed"))


@pytest.mark.parametrize("completed", [0, 2])
def test_a_load_fails_only_with_a_failed_stage(s, feed, completed):
    load = new_load(s, feed)
    for stage in STAGES[:completed]:
        add_stage(s, load, stage)
    answered_by(s, "cannot fail without a failed stage", MOVE, {"status": "failed_rolled_back", "id": load},
                status_guard("loads: load % cannot fail without a failed stage"))
    add_stage(s, load, STAGES[completed], "failed_rolled_back")
    s.execute(text(MOVE), {"status": "failed_rolled_back", "id": load})  # with its failed stage it moves


def test_a_failed_load_has_no_standard_record(s, feed):
    load = new_load(s, feed)
    for stage in STAGES[:3]:
        add_stage(s, load, stage)
    add_stage(s, load, "standard", "failed_rolled_back")
    s.execute(text(RECORD), {"load": load, "feed": feed, "row": 1, "vals": "{}"})
    answered_by(s, "cannot fail with standard records", MOVE, {"status": "failed_rolled_back", "id": load},
                status_guard("loads: load % cannot fail with standard records"))


# ---------------------------------------------------------------- G9: one received load per feed

DROP_ONE_RECEIVED = "DROP INDEX uq_loads_one_received_per_feed"


def test_a_feed_has_one_received_load_at_a_time(s, feed):
    new_load(s, feed)
    answered_by(s, "uq_loads_one_received_per_feed", LOAD, load_params(feed), DROP_ONE_RECEIVED)
    new_load(s, a_registered_feed(s, "syn_load_guards_other"))  # another feed has its own


@pytest.mark.parametrize("make", [loaded_load, failed_load])
def test_the_next_load_may_come_once_the_last_one_finished(s, feed, make):
    make(s, feed)  # finished: loaded, or failed and rolled back
    new_load(s, feed)
    answered_by(s, "uq_loads_one_received_per_feed", LOAD, load_params(feed), DROP_ONE_RECEIVED)


# ---------------------------------------------------------------- G10: stages are written in order, while received


def stage_guard(message):
    return _branch_off("amaze_guard_load_stage", message)


@pytest.mark.parametrize("make", [loaded_load, failed_load])
def test_a_stage_is_written_only_while_the_load_is_received(s, feed, make):
    load = make(s, feed)
    answered_by(s, "written only while it is received", STAGE, stage_params(load, "standard"),
                stage_guard("load_stages: load % is %, and stages are written only while it is received"))


@pytest.mark.parametrize(("done", "stage"), [
    ((), "curated"), ((), "validated"), ((), "standard"),          # the first stage is raw
    (("raw",), "validated"), (("raw", "curated"), "standard"),     # none is skipped
    (("raw",), "raw")])                                            # none is written twice
def test_stages_come_in_order(s, feed, done, stage):
    load = new_load(s, feed)
    for earlier in done:
        add_stage(s, load, earlier)
    answered_by(s, "comes out of order", STAGE, stage_params(load, stage),
                stage_guard("load_stages: stage % of load % comes out of order"))


def test_no_stage_follows_a_failed_one(s, feed):
    load = new_load(s, feed)
    add_stage(s, load, "raw", "failed_rolled_back")
    answered_by(s, "comes out of order", STAGE, stage_params(load, "curated"),
                stage_guard("load_stages: stage % of load % comes out of order"))


def test_the_four_stages_in_order_are_accepted(s, feed):
    load = new_load(s, feed)
    answered_by(s, "comes out of order", STAGE, stage_params(load, "curated"),
                stage_guard("load_stages: stage % of load % comes out of order"))
    for stage in STAGES:
        add_stage(s, load, stage)


@pytest.mark.parametrize(("column", "value", "name"), [
    ("stage", "bogus", "ck_load_stages_stage"), ("outcome", "ok", "ck_load_stages_outcome"),
    ("rows_in", -1, "ck_load_stages_counts"), ("rows_out", -1, "ck_load_stages_counts")])
def test_a_stage_row_has_known_names_and_counts(s, feed, column, value, name):
    load = new_load(s, feed)
    answered_by(s, name, STAGE, {**stage_params(load, "raw"), column: value},
                f"ALTER TABLE load_stages DROP CONSTRAINT {name}")


# ---------------------------------------------------------------- G11: a record belongs to a received load of its feed


def record_guard(message):
    return _branch_off("amaze_guard_standard_record", message)


@pytest.mark.parametrize("make", [loaded_load, failed_load])
def test_a_record_is_written_only_while_the_load_is_received(s, feed, make):
    load = make(s, feed)
    answered_by(s, "written only while it is received", RECORD,
                {"load": load, "feed": feed, "row": 1, "vals": "{}"},
                record_guard("standard_records: load % is %, and records are written only while it is received"))


def test_a_record_belongs_to_the_feed_of_its_load(s, feed):
    load = new_load(s, feed)
    other = a_registered_feed(s, "syn_load_guards_other")
    answered_by(s, "belongs to feed", RECORD, {"load": load, "feed": other, "row": 1, "vals": "{}"},
                record_guard("standard_records: load % belongs to feed %, not %"))
    s.execute(text(RECORD), {"load": load, "feed": feed, "row": 1, "vals": "{}"})  # its own feed's is accepted


def test_a_record_has_a_row_number_from_one(s, feed):
    load = new_load(s, feed)
    answered_by(s, "ck_standard_records_row_number", RECORD, {"load": load, "feed": feed, "row": 0, "vals": "{}"},
                "ALTER TABLE standard_records DROP CONSTRAINT ck_standard_records_row_number")
