"""Migration 0019's load guards (phase 7 plan, section 6: G12 and G13), by raw SQL.

G12 (trg_loads_b_arrival, amaze_guard_load_arrival): the four arrival fields of a load. G13 (trg_loads_a_status,
amaze_guard_load_status, replaced): the new ways a load's status moves. The repositories are bypassed on purpose,
so only the database decides. Each refusal goes through answered_by(): it is refused with the guard's message,
and with only that guard branch removed (in a savepoint rolled back at once) it is no longer refused with that
message. The messages below are the contract of 0019; each is unique inside its function. G14 to G16 are in
test_db_certification_guards.py, which uses the helpers at the top of this file. Against 0018 every test fails.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from tests.configured import a_registered_feed, go_live
from tests.test_db_load_guards import LOAD, MOVE, STAGES, add_stage, load_params, new_load, status_guard
from tests.test_db_onboarding_guards import _branch_off, answered_by

SHA = "a" * 64
ARRIVE = ("UPDATE loads SET content_sha256 = :sha, arrival_outcome = :outcome, "
          "arrival_detail = CAST(:detail AS jsonb), arrival_profile_id = :profile WHERE id = :id")
ABANDON = "UPDATE loads SET status = 'abandoned', finished_at = :finished, failure = :failure WHERE id = :id"
FLAGGED = ('INSERT INTO standard_records (load_id, feed_id, row_number, "values", flags) '
           "VALUES (:load, :feed, :row, '{}', CAST(:flags AS text[]))")
CERT = ('INSERT INTO load_certifications (load_id, feed_id, "rows", flagged_rows, rule_counts, trust_score, '
        "threshold, outcome) VALUES (:load, :feed, :rows, :flagged, CAST(:counts AS jsonb), :score, "
        ":threshold, :outcome)")


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def live(s):
    """(feed, go_live_id): a registered feed with a go-live, which a scheduled load names."""
    feed = a_registered_feed(s, "syn_arrival_guards")
    return feed, go_live(s, feed).id


def scheduled(s, live) -> uuid.UUID:
    """A received scheduled load."""
    feed, gl = live
    return s.execute(text(LOAD), load_params(feed, purpose="scheduled", go_live=gl)).scalar_one()


def arrive_params(load, outcome="on_time_complete", *, sha=SHA, detail=None, profile=None):
    return {"id": load, "sha": sha, "outcome": outcome, "detail": json.dumps(detail or {}), "profile": profile}


def arrive(s, load, outcome="on_time_complete", **over) -> None:
    s.execute(text(ARRIVE), arrive_params(load, outcome, **over))


def profile_of(s, feed) -> uuid.UUID:
    return s.scalar(text("SELECT id FROM feed_profiles WHERE feed_id = :f ORDER BY version DESC LIMIT 1"),
                    {"f": feed})


def abandon_params(load, *, finished=True, failure="Synthetic."):
    return {"id": load, "finished": datetime.now(UTC) if finished else None, "failure": failure}


def scheduled_loaded(s, live, *, rows=10, flagged=0, outcome="on_time_complete") -> uuid.UUID:
    """A scheduled load with its arrival, `rows` standard records (the first `flagged` flagged), four completed
    stages, moved to loaded: the state step 15 certifies."""
    feed, _ = live
    load = scheduled(s, live)
    arrive(s, load, outcome, profile=profile_of(s, feed) if outcome == "additive_parked" else None)
    for row in range(1, rows + 1):
        s.execute(text(FLAGGED), {"load": load, "feed": feed, "row": row,
                                  "flags": "{synthetic_rule}" if row <= flagged else "{}"})
    for stage in STAGES:
        add_stage(s, load, stage)
    s.execute(text(MOVE), {"status": "loaded", "id": load})
    return load


def cert_params(load, feed, *, rows=10, flagged=0, threshold=90.0, **over):
    score = round(100 * (rows - flagged) / rows, 1)
    return {"load": load, "feed": feed, "rows": rows, "flagged": flagged, "counts": json.dumps({}), "score": score,
            "threshold": threshold, "outcome": "released" if score >= threshold else "held_consumers_warned", **over}


def certified(s, live, **kw) -> uuid.UUID:
    """A scheduled loaded load with a certification of the outcome its score gives."""
    load = scheduled_loaded(s, live, **kw)
    s.execute(text(CERT), cert_params(load, live[0], **{k: v for k, v in kw.items() if k in ("rows", "flagged")}))
    return load


def arrival_guard(message):
    return _branch_off("amaze_guard_load_arrival", message)


# ---------------------------------------------------------------- G12: the arrival fields of a load

FIELDS = {"content_sha256": ("content_sha256 = :v", SHA), "arrival_outcome": ("arrival_outcome = :v", "on_time_complete"),
          "arrival_detail": ("arrival_detail = CAST(:v AS jsonb)", "{}")}


@pytest.mark.parametrize("field", sorted(FIELDS))
def test_a_go_live_test_load_has_no_arrival(s, live, field):
    load = new_load(s, live[0])
    assignment, value = FIELDS[field]
    answered_by(s, "has no arrival", f"UPDATE loads SET {assignment} WHERE id = :id", {"id": load, "v": value},
                arrival_guard("loads: load % is a go_live_test load, and a go_live_test load has no arrival"))


def test_a_scheduled_load_takes_its_arrival_while_received(s, live):
    load = scheduled(s, live)
    arrive(s, load)
    assert s.scalar(text("SELECT (arrival_outcome, content_sha256) FROM loads WHERE id = :id"), {"id": load}) == (
        "on_time_complete", SHA)


@pytest.mark.parametrize("state", ["quarantined", "loaded"])
def test_an_arrival_is_set_only_while_the_load_is_received(s, live, state):
    if state == "loaded":
        load = scheduled_loaded(s, live)
    else:
        load = scheduled(s, live)
        arrive(s, load, "resent_quarantined")
        s.execute(text(MOVE), {"status": "quarantined", "id": load})
    answered_by(s, "its arrival is set only while it is received", ARRIVE,
                arrive_params(load, "on_time_complete", sha="b" * 64),
                arrival_guard("loads: load % is %, and its arrival is set only while it is received"))


@pytest.mark.parametrize("change", [{"outcome": "breaking_held"}, {"sha": "b" * 64}, {"detail": {"added": ["x"]}}])
def test_an_arrival_does_not_change_once_set(s, live, change):
    load = scheduled(s, live)
    arrive(s, load, detail={})
    answered_by(s, "does not change once set", ARRIVE, arrive_params(load, **{"outcome": "on_time_complete", **change}),
                arrival_guard("loads: the arrival of load % does not change once set"))
    arrive(s, load, detail={})  # the same values again are not a change


def test_an_arrival_profile_goes_with_additive_parked_only(s, live):
    load = scheduled(s, live)
    answered_by(s, "but its arrival outcome is", ARRIVE,
                arrive_params(load, "on_time_complete", profile=profile_of(s, live[0])),
                arrival_guard("loads: load % has an arrival profile, but its arrival outcome is %, not additive_parked"))
    arrive(s, load, "additive_parked", profile=profile_of(s, live[0]))  # with additive_parked it is accepted


def test_an_arrival_profile_is_a_profile_of_the_loads_feed(s, live):
    load = scheduled(s, live)
    other = a_registered_feed(s, "syn_arrival_guards_other")
    answered_by(s, "is not a profile of feed", ARRIVE, arrive_params(load, "additive_parked", profile=profile_of(s, other)),
                arrival_guard("loads: arrival profile % is not a profile of feed %"))


@pytest.mark.parametrize(("name", "column", "bad"), [
    ("ck_loads_content_sha256", "sha", "A" * 64), ("ck_loads_content_sha256", "sha", "a" * 63),
    ("ck_loads_arrival_outcome", "outcome", "late")])
def test_the_arrival_fields_have_known_values(s, live, name, column, bad):
    load = scheduled(s, live)
    answered_by(s, name, ARRIVE, {**arrive_params(load), column: bad}, f"ALTER TABLE loads DROP CONSTRAINT {name}")


def test_a_load_with_no_arrival_passes_the_arrival_checks(s, live):
    """The NULL case of both checks: an unarrived load (every load of phase 6) has NULL in all four fields."""
    load = scheduled(s, live)
    assert s.scalar(text("SELECT count(*) FROM loads WHERE id = :id AND content_sha256 IS NULL "
                         "AND arrival_outcome IS NULL AND arrival_detail IS NULL AND arrival_profile_id IS NULL"),
                    {"id": load}) == 1
    answered_by(s, "ck_loads_arrival_outcome", ARRIVE, arrive_params(load, "late"),
                "ALTER TABLE loads DROP CONSTRAINT ck_loads_arrival_outcome")


# ---------------------------------------------------------------- G13: the new ways a load's status moves


def with_stages(s, load, count) -> None:
    for stage in STAGES[:count]:
        add_stage(s, load, stage)


@pytest.mark.parametrize("outcome", ["on_time_complete", "additive_parked"])
def test_a_scheduled_load_with_a_passing_arrival_is_loaded(s, live, outcome):
    assert s.scalar(text("SELECT status FROM loads WHERE id = :id"),
                    {"id": scheduled_loaded(s, live, outcome=outcome)}) == "loaded"


@pytest.mark.parametrize("to", ["loaded", "failed_rolled_back"])
@pytest.mark.parametrize("outcome", [None, "resent_quarantined", "breaking_held"])
def test_a_scheduled_load_moves_on_only_after_a_passing_arrival(s, live, outcome, to):
    load = scheduled(s, live)
    if outcome:
        arrive(s, load, outcome)
    if to == "loaded":
        with_stages(s, load, 4)
    else:
        add_stage(s, load, "raw", "failed_rolled_back")
    answered_by(s, "has no passing arrival check", MOVE, {"status": to, "id": load},
                status_guard("loads: scheduled load % has no passing arrival check (its arrival outcome is %)"))


def test_a_resent_delivery_is_quarantined(s, live):
    load = scheduled(s, live)
    arrive(s, load, "resent_quarantined")
    s.execute(text(MOVE), {"status": "quarantined", "id": load})
    assert s.scalar(text("SELECT status FROM loads WHERE id = :id"), {"id": load}) == "quarantined"


def test_a_load_is_quarantined_only_as_resent_quarantined(s, live):
    load = scheduled(s, live)
    arrive(s, load, "on_time_complete")
    answered_by(s, "can be quarantined only as resent_quarantined", MOVE, {"status": "quarantined", "id": load},
                status_guard("loads: load % can be quarantined only as resent_quarantined, not %"))


def test_a_breaking_delivery_is_held_on_arrival(s, live):
    load = scheduled(s, live)
    arrive(s, load, "breaking_held")
    s.execute(text(MOVE), {"status": "held", "id": load})
    assert s.scalar(text("SELECT status FROM loads WHERE id = :id"), {"id": load}) == "held"


@pytest.mark.parametrize("outcome", [None, "on_time_complete", "resent_quarantined"])
def test_a_received_load_is_held_only_as_breaking_held(s, live, outcome):
    load = scheduled(s, live)
    if outcome:
        arrive(s, load, outcome)
    answered_by(s, "can be held on arrival only as breaking_held", MOVE, {"status": "held", "id": load},
                status_guard("loads: load % can be held on arrival only as breaking_held, not %"))


@pytest.mark.parametrize(("to", "outcome"), [("quarantined", "resent_quarantined"), ("held", "breaking_held"),
                                             ("abandoned", None)])
def test_a_load_that_is_not_loaded_has_no_stage(s, live, to, outcome):
    load = scheduled(s, live)
    if outcome:
        arrive(s, load, outcome)
    add_stage(s, load, "raw")
    sql, params = (ABANDON, abandon_params(load)) if to == "abandoned" else (MOVE, {"status": to, "id": load})
    answered_by(s, "with stages", sql, params, status_guard("loads: load % cannot be % with stages"))


def test_a_stale_load_is_abandoned_with_its_failure(s, live):
    load = scheduled(s, live)
    s.execute(text(ABANDON), abandon_params(load))
    assert s.scalar(text("SELECT status FROM loads WHERE id = :id"), {"id": load}) == "abandoned"
    s.execute(text(ABANDON), abandon_params(new_load(s, live[0])))  # a go_live_test load is abandoned the same way


@pytest.mark.parametrize("missing", ["finished", "failure"])
def test_an_abandoned_load_has_a_failure_and_a_finish_time(s, live, missing):
    load = scheduled(s, live)
    answered_by(s, "without a failure and a finish time", ABANDON,
                abandon_params(load, **{missing: False if missing == "finished" else None}),
                status_guard("loads: load % cannot be abandoned without a failure and a finish time"))


def test_an_abandoned_load_has_no_standard_record(s, live):
    load = scheduled(s, live)
    s.execute(text(FLAGGED), {"load": load, "feed": live[0], "row": 1, "flags": "{}"})
    answered_by(s, "cannot be abandoned with standard records", ABANDON, abandon_params(load),
                status_guard("loads: load % cannot be abandoned with standard records"))


def test_a_received_load_moves_only_to_the_five_statuses(s, live):
    load = scheduled(s, live)
    answered_by(s, "moves only to loaded, failed_rolled_back, quarantined, held or abandoned", MOVE,
                {"status": "released", "id": load},
                status_guard("loads: a received load moves only to loaded, failed_rolled_back, quarantined, held or "
                             "abandoned, not %"))


@pytest.mark.parametrize("state", ["quarantined", "held", "abandoned"])
@pytest.mark.parametrize("to", ["loaded", "released", "received"])
def test_a_load_that_ended_on_arrival_does_not_move(s, live, state, to):
    load = scheduled(s, live)
    if state == "abandoned":
        s.execute(text(ABANDON), abandon_params(load))
    else:
        arrive(s, load, "resent_quarantined" if state == "quarantined" else "breaking_held")
        s.execute(text(MOVE), {"status": state, "id": load})
    answered_by(s, "does not move", MOVE, {"status": to, "id": load},
                status_guard("loads: load % is %, and a finished load does not move"))


@pytest.mark.parametrize("to", ["released", "held_consumers_warned"])
def test_a_certified_load_is_released_or_held_as_certified(s, live, to):
    rows = {"released": {"flagged": 0}, "held_consumers_warned": {"flagged": 5}}[to]
    load = certified(s, live, **rows)
    status = "released" if to == "released" else "held"
    s.execute(text(MOVE), {"status": status, "id": load})
    assert s.scalar(text("SELECT status FROM loads WHERE id = :id"), {"id": load}) == status


@pytest.mark.parametrize(("status", "flagged"), [("released", 5), ("held", 0)])
def test_a_load_moves_only_to_the_status_its_certification_says(s, live, status, flagged):
    load = certified(s, live, flagged=flagged)
    answered_by(s, "without a certification that says so", MOVE, {"status": status, "id": load},
                status_guard("loads: load % cannot be % without a certification that says so"))


@pytest.mark.parametrize("status", ["released", "held"])
def test_a_load_is_released_or_held_only_after_it_is_certified(s, live, status):
    load = scheduled_loaded(s, live)
    answered_by(s, "without a certification that says so", MOVE, {"status": status, "id": load},
                status_guard("loads: load % cannot be % without a certification that says so"))


@pytest.mark.parametrize("status", ["released", "held"])
def test_a_go_live_test_load_is_never_released_or_held(s, live, status):
    load = new_load(s, live[0])
    with_stages(s, load, 4)
    s.execute(text(MOVE), {"status": "loaded", "id": load})
    answered_by(s, "only a scheduled load is released or held", MOVE, {"status": status, "id": load},
                status_guard("loads: load % is a go_live_test load, and only a scheduled load is released or held"))
