"""The run engine, step 13 (phase 6 plan, assumptions 14 to 20; phase 5 plan section 16 notes 1 and 2).

raw -> curated -> validated -> standard, each stage a load_stages row; one standard record per file row; a failed
file leaves no standard record (failed_rolled_back). No model call: the boundary test covers core/engine. The
engine reads only MappingRepository.current_for_feed() and RuleRepository.current_for_feed().
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.engine.run import FailedRolledBack, RunResult, StageCompleted
from core.repositories.loads import LoadRepository
from core.repositories.standard_records import StandardRecordRepository
from ports.storage.local import LocalBlobStore
from tests.configured import a_registered_feed, approved_mapping, approved_rule, go_live
from tests.step_chains import run_engine

KEY = "raw/syn_engine/f.csv"
HEADER = "instr_id,trade_date,traded_qty,px,note\n"
GOOD = HEADER + "syn-0001,2026-11-02,120500,10.25,a\nsyn-0002,2026-11-02,98200,,b\nsyn-0003,2026-11-03,100,3,c\n"
COLUMNS = (("instr_id", "instrument_id"), ("trade_date", "price_date"), ("traded_qty", "volume"),
           ("px", "close_price"))  # the column `note` stays unmapped
PASS_THROUGH = {"mastering": "pass_through", "transformations": "none"}


@pytest.fixture
def world(db_session, glossary, tmp_path):
    feed = a_registered_feed(db_session, "syn_engine")
    for field, key in COLUMNS:
        approved_mapping(db_session, feed, field, key)
    blobs = LocalBlobStore(tmp_path / "blob")
    blobs.put(KEY, GOOD.encode())
    return SimpleNamespace(feed=feed, blobs=blobs, session=db_session)


def run(world, *, file_ref=KEY, purpose="go_live_test", go_live_id=None, **kw):
    return run_engine(world.session, world.blobs, world.feed, file_ref=file_ref, purpose=purpose,
                      go_live_id=go_live_id, **kw)


def stages_of(world, load):
    return [(s.stage, s.outcome, s.rows_in, s.rows_out) for s in LoadRepository(world.session).stages(load.id)]


def test_each_stage_completes_and_is_tracked(world):
    result = run(world)
    assert [r.outcome for r in result.stages] == ["stage_completed"] * 4
    assert [r.stage for r in result.stages] == ["raw", "curated", "validated", "standard"]
    assert result.load.status == "loaded" and result.load.failed_stage is None
    assert stages_of(world, result.load) == [("raw", "stage_completed", 3, 3), ("curated", "stage_completed", 3, 3),
                                             ("validated", "stage_completed", 3, 3),
                                             ("standard", "stage_completed", 3, 3)]
    assert StandardRecordRepository(world.session).count(result.load.id) == 3


def test_the_standard_model_holds_the_mapped_columns_as_json_under_their_element_keys(world):
    result = run(world)
    first, second, third = StandardRecordRepository(world.session).list_for_load(result.load.id)
    assert first.values == {"instrument_id": "syn-0001", "price_date": "2026-11-02", "volume": 120500,
                            "close_price": "10.2500000000"}  # a decimal as an exact string, a date as ISO
    assert second.values["close_price"] is None  # an empty cell is NULL
    assert (third.values["volume"], third.values["close_price"]) == (100, "3.0000000000")
    assert [r.row_number for r in (first, second, third)] == [1, 2, 3]


def test_only_current_mappings_are_loaded_and_an_unmapped_column_is_dropped(world):
    """Phase 5 note 1: the engine reads only MappingRepository.current_for_feed()."""
    approved_mapping(world.session, world.feed, "px", "mid_price")  # version 2 of the column px
    result = run(world)
    curated = result.stages[1]
    assert curated.detail["unmapped_columns"] == ["note"]
    assert curated.detail["mapped_columns"] == {"instr_id": "instrument_id", "trade_date": "price_date",
                                                "traded_qty": "volume", "px": "mid_price"}
    (record,) = StandardRecordRepository(world.session).list_for_load(result.load.id, limit=1)
    assert set(record.values) == {"instrument_id", "price_date", "volume", "mid_price"}  # note and close_price: no


def test_the_standard_stage_says_pass_through(world):
    """Phase 6 plan, assumption 18: mastering and transformations are marked as the minimal pass-through."""
    assert run(world).stages[3].detail == PASS_THROUGH


def test_a_value_that_does_not_cast_fails_the_file_and_leaves_no_record(world):
    """Phase 5 note 2, assumption 16: a non-empty value that does not cast to its element's type holds the whole
    file; rows are neither rejected nor nulled."""
    world.blobs.put(KEY, (HEADER + "syn-0001,2026-11-02,12x,10.25,a\nsyn-0002,not-a-date,98200,1,b\n"
                                   "syn-0003,2026-11-03,100,,c\n").encode())
    result = run(world)
    failed = result.stages[-1]
    assert isinstance(failed, FailedRolledBack) and failed.stage == "curated"
    assert failed.reason == "values_do_not_cast"
    assert failed.detail["count"] == 2
    assert failed.detail["first"] == [{"row": 1, "column": "traded_qty", "value": "12x"},
                                      {"row": 2, "column": "trade_date", "value": "not-a-date"}]
    assert [r.outcome for r in result.stages] == ["stage_completed", "failed_rolled_back"]
    assert (result.load.status, result.load.failed_stage) == ("failed_rolled_back", "curated")
    assert result.load.failure and "do not cast" in result.load.failure
    assert StandardRecordRepository(world.session).count(result.load.id) == 0
    assert stages_of(world, result.load) == [("raw", "stage_completed", 3, 3), ("curated", "failed_rolled_back", 3, 0)]


def test_only_the_first_five_uncast_values_are_listed(world):
    rows = "".join(f"syn-{n:04d},2026-11-02,x{n},1,a\n" for n in range(8))
    world.blobs.put(KEY, (HEADER + rows).encode())
    failed = run(world).stages[-1]
    assert failed.detail["count"] == 8 and len(failed.detail["first"]) == 5


def test_a_rule_flags_rows_without_failing_the_load(world):
    """Assumption 17: rules flag, they do not fail; the validated stage counts the flags per rule."""
    approved_rule(world.session, world.feed, "volume_over_100000", sql="SELECT _row FROM curated WHERE volume > 100000",
                  description="Volume is at most 100000.")
    approved_rule(world.session, world.feed, "price_present", sql="SELECT _row FROM curated WHERE close_price IS NULL",
                  description="Price is present.")
    result = run(world)
    assert result.load.status == "loaded"
    assert result.stages[2].detail["rules"] == {"price_present": 1, "volume_over_100000": 1}
    flags = {r.row_number: r.flags for r in StandardRecordRepository(world.session).list_for_load(result.load.id)}
    assert flags == {1: ["volume_over_100000"], 2: ["price_present"], 3: []}


def test_a_rule_that_errors_at_load_time_fails_the_validated_stage(world):
    approved_rule(world.session, world.feed, "broken", sql="SELECT _row FROM curated WHERE no_such_column = 1",
                  description="Broken on purpose.")
    result = run(world)
    failed = result.stages[-1]
    assert (failed.stage, failed.reason) == ("validated", "rule_failed")
    assert failed.detail["rule"] == "broken" and "no_such_column" in failed.detail["error"]
    assert [r.stage for r in result.stages] == ["raw", "curated", "validated"]
    assert result.load.status == "failed_rolled_back"
    assert StandardRecordRepository(world.session).count(result.load.id) == 0


def test_a_rule_that_reads_outside_the_file_fails_the_validated_stage(world):
    approved_rule(world.session, world.feed, "peeks", sql="SELECT _row FROM read_csv('.env')",
                  description="Reaches outside the file.")
    failed = run(world).stages[-1]
    assert (failed.stage, failed.reason) == ("validated", "rule_failed") and "Permission" in failed.detail["error"]


def test_a_failed_write_is_rolled_back(world, monkeypatch):
    """Assumption 19: if writing the records fails, that write is rolled back and a new transaction writes the
    completed stages, a failed standard stage and failed_rolled_back. No standard record survives."""

    def boom(self, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(StandardRecordRepository, "add_many", boom)
    result = run(world)
    assert [(r.stage, r.outcome) for r in result.stages] == [
        ("raw", "stage_completed"), ("curated", "stage_completed"), ("validated", "stage_completed"),
        ("standard", "failed_rolled_back")]
    assert "disk full" in result.stages[-1].reason
    assert (result.load.status, result.load.failed_stage) == ("failed_rolled_back", "standard")
    assert StandardRecordRepository(world.session).count(result.load.id) == 0


def test_a_missing_file_fails_raw(world):
    result = run(world, file_ref="raw/syn_engine/gone.csv")
    assert [(r.stage, r.reason) for r in result.stages if isinstance(r, FailedRolledBack)] == [("raw", "file_missing")]
    assert result.load.failed_stage == "raw"


def test_a_file_with_no_data_rows_fails_raw(world):
    world.blobs.put(KEY, HEADER.encode())
    assert [(r.stage, r.reason) for r in run(world).stages] == [("raw", "no_data_rows")]


def test_a_file_that_is_not_a_table_fails_raw(world):
    world.blobs.put(KEY, b"")
    assert run(world).stages[-1].stage == "raw"


def test_a_mapped_column_missing_from_the_file_fails_curated(world):
    world.blobs.put(KEY, b"instr_id,trade_date\nsyn-0001,2026-11-02\n")
    failed = run(world).stages[-1]
    assert (failed.stage, failed.reason) == ("curated", "mapped_column_missing")
    assert failed.detail["missing"] == ["px", "traded_qty"]  # in source-field order


def test_a_header_in_another_case_still_matches_its_mapping(world):
    world.blobs.put(KEY, GOOD.replace("instr_id", "INSTR_ID").encode())
    assert run(world).load.status == "loaded"


def test_a_feed_with_no_current_mapping_fails_curated(db_session, glossary, tmp_path):
    feed = a_registered_feed(db_session, "syn_engine_unmapped")
    blobs = LocalBlobStore(tmp_path / "blob")
    blobs.put(KEY, GOOD.encode())
    result = run_engine(db_session, blobs, feed, file_ref=KEY, purpose="go_live_test")
    assert (result.stages[-1].stage, result.stages[-1].reason) == ("curated", "no_mapped_columns")


def test_a_configuration_changed_since_go_live_fails_at_raw(world):
    """Assumption 15: a scheduled load runs the configuration the latest go-live approved; if the current mappings
    or rules differ, it fails at raw and a new go-live is needed."""
    live = go_live(world.session, world.feed)
    ok = run(world, purpose="scheduled", go_live_id=live.id)
    assert ok.load.status == "loaded" and ok.load.go_live_id == live.id
    approved_mapping(world.session, world.feed, "px", "mid_price")  # a steward re-maps after go-live
    changed = run(world, purpose="scheduled", go_live_id=live.id)
    failed = changed.stages[-1]
    assert [(r.stage, r.outcome) for r in changed.stages] == [("raw", "failed_rolled_back")]
    assert failed.reason == "configuration_changed_since_go_live"
    assert changed.load.failed_stage == "raw"
    approved_rule(world.session, world.feed, "late_rule", sql="SELECT _row FROM curated", description="A new rule.")
    assert run(world, purpose="scheduled", go_live_id=live.id).stages[-1].reason == "configuration_changed_since_go_live"


def test_a_go_live_test_load_is_not_held_to_a_go_live(world):
    live = go_live(world.session, world.feed)
    approved_mapping(world.session, world.feed, "px", "mid_price")
    assert live and run(world).load.status == "loaded"  # the test load of the next go-live


def test_the_results_are_typed_and_round_trip():
    from pydantic import TypeAdapter

    adapter = TypeAdapter(RunResult)
    for value in (StageCompleted(stage="raw", rows_in=1, rows_out=1, detail={}),
                  FailedRolledBack(stage="curated", reason="values_do_not_cast", detail={"count": 1})):
        assert adapter.validate_python(adapter.dump_python(value)) == value
