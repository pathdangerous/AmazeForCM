"""Task 4.2 (phase 7.8, assumptions 28 and 29): the run engine applies the approved transformations and the feed's
duplicate-key policy. Deterministic code only: the steps are fixed SQL templates (core/engine/transform.py), the
policies are plain code. Mastering stays a pass-through (decision D6)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from pydantic import TypeAdapter

from core.domain.transformation import TransformStep
from core.engine.run import FailedRolledBack
from core.engine.transform import column_sql, step_sql
from core.intake import canonical_csv, read_xlsx_bytes
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feed_signoffs import FeedSignoffRepository
from core.repositories.feeds import FeedRepository
from core.repositories.go_lives import GoLiveRepository
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.standard_records import StandardRecordRepository
from core.repositories.transformations import KeyPolicyRepository
from core.steps.s15_certify import trust_score
from ports.storage.local import LocalBlobStore
from tests.configured import (
    a_registered_feed,
    approved_element,
    approved_mapping,
    approved_transformation,
    go_live,
    new_element,
)
from tests.conftest import dev_user_for
from tests.onboarded import BACKEND, profiled
from tests.step_chains import run_engine

STEP = TypeAdapter(TransformStep)
KEY = "raw/syn_transform/f.csv"
DEMO = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic" / "Synthetic_Security_Master.xlsx"


def step(**kw):
    return STEP.validate_python(kw)


def sql_value(expr_sql: str, value: str | None):
    """The template run on one text value, in DuckDB."""
    con = duckdb.connect(":memory:")
    try:
        return con.execute(f"SELECT {expr_sql} FROM (SELECT $v::VARCHAR AS v) t", {"v": value}).fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------- the templates


def test_trim():
    assert sql_value(step_sql("v", step(op="trim")), "  a b  ") == "a b"
    assert sql_value(step_sql("v", step(op="trim")), None) is None


def test_placeholder_to_null_is_case_insensitive_and_trimmed():
    sql = step_sql("v", step(op="placeholder_to_null", values=["n/a", "ALL"]))
    assert sql_value(sql, "N/A") is None
    assert sql_value(sql, "  all ") is None
    assert sql_value(sql, "All") is None
    assert sql_value(sql, "alliance") == "alliance"  # a whole value, not a substring
    assert sql_value(sql, " keep ") == " keep "  # untouched, not trimmed


def test_a_placeholder_shaped_like_sql_is_a_harmless_literal():
    evil = "');DROP TABLE x"  # 16 characters is the longest a placeholder may be
    sql = step_sql("v", step(op="placeholder_to_null", values=[evil, "it's"]))
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE TABLE x AS SELECT 1 AS a")
        assert con.execute("SELECT $q::VARCHAR", {"q": evil}).fetchone()[0] == evil
        for value, expected in ((evil, None), ("IT'S", None), ("other", "other")):
            assert con.execute(f"SELECT {sql} FROM (SELECT $v::VARCHAR AS v) t", {"v": value}).fetchone()[0] == expected
        assert con.execute("SELECT count(*) FROM x").fetchone()[0] == 1  # x still exists
    finally:
        con.close()


@pytest.mark.parametrize("fmt, text", [("DD-MM-YYYY", "31-12-2019"), ("MM-DD-YYYY", "12-31-2019"),
                                       ("DD/MM/YYYY", "31/12/2019"), ("MM/DD/YYYY", "12/31/2019"),
                                       ("YYYY-MM-DD", "2019-12-31"), ("YYYYMMDD", "20191231"),
                                       ("DD.MM.YYYY", "31.12.2019")])
def test_parse_date_for_every_format(fmt, text):
    sql = step_sql("v", step(op="parse_date", format=fmt))
    assert str(sql_value(sql, text)) == "2019-12-31"
    assert sql_value(sql, "not a date") is None  # NULL, found by the uncast check; never an exception
    assert sql_value(sql, None) is None


def test_cast():
    assert sql_value(step_sql("v", step(op="cast", type="integer")), "12") == 12
    assert sql_value(step_sql("v", step(op="cast", type="integer")), "12x") is None
    assert str(sql_value(step_sql("v", step(op="cast", type="decimal")), "1.50")) == "1.5000000000"
    assert sql_value(step_sql("v", step(op="cast", type="boolean")), "true") is True


def test_rename_is_no_sql():
    assert step_sql("v", step(op="rename", to="last_price")) == "v"


def test_steps_compose_in_order():
    steps = [step(op="trim"), step(op="placeholder_to_null", values=["na"]), step(op="parse_date", format="DD-MM-YYYY")]
    sql = column_sql("Rec Date", steps)
    con = duckdb.connect(":memory:")
    try:
        con.execute('CREATE TABLE raw AS SELECT * FROM (VALUES (\' 31-12-2019 \'), (\' NA \'), (\'??\')) t("Rec Date")')
        got = [r[0] for r in con.execute(f'SELECT {sql} FROM raw').fetchall()]
    finally:
        con.close()
    assert [str(g) if g is not None else None for g in got] == ["2019-12-31", None, None]
    # the order matters: the placeholder is compared after the trim
    assert column_sql("c", [step(op="trim")]) == step_sql('"c"', step(op="trim"))


# ---------------------------------------------------------------- the engine


HEADER = "instr_id,trade_date,traded_qty,px\n"
COLUMNS = (("instr_id", "instrument_id"), ("trade_date", "price_date"), ("traded_qty", "volume"),
           ("px", "close_price"))


@pytest.fixture
def world(db_session, glossary, tmp_path):
    feed = a_registered_feed(db_session, "syn_transform")
    for field, key in COLUMNS:
        approved_mapping(db_session, feed, field, key)
    blobs = LocalBlobStore(tmp_path / "blob")
    return SimpleNamespace(feed=feed, blobs=blobs, session=db_session)


def run(world, text: str, **kw):
    world.blobs.put(KEY, text.encode())
    return run_engine(world.session, world.blobs, world.feed, file_ref=KEY, purpose=kw.pop("purpose", "go_live_test"),
                      **kw)


def records(world, load):
    return StandardRecordRepository(world.session).list_for_load(load.id, limit=1000)


def test_a_placeholder_nulled_by_an_approved_transform_does_not_fail_the_file(world):
    approved_transformation(world.session, world.feed, "px", [{"op": "placeholder_to_null", "values": ["n/a", "-"]}])
    result = run(world, HEADER + "a,2026-11-02,1,N/A\nb,2026-11-02,2,-\nc,2026-11-02,3,4.5\n")
    assert result.load.status == "loaded"
    assert [r.values["close_price"] for r in records(world, result.load)] == [None, None, "4.5000000000"]
    assert result.stages[3].detail == {"mastering": "pass_through", "transformations": "approved"}


def test_without_the_transform_the_same_placeholder_still_fails_the_file(world):
    result = run(world, HEADER + "a,2026-11-02,1,N/A\n")
    assert result.load.status == "failed_rolled_back" and result.stages[-1].reason == "values_do_not_cast"


def test_a_value_that_is_not_a_placeholder_still_fails_the_file(world):
    approved_transformation(world.session, world.feed, "px", [{"op": "placeholder_to_null", "values": ["n/a"]}])
    result = run(world, HEADER + "a,2026-11-02,1,N/A\nb,2026-11-02,2,twelve\n")
    failed = result.stages[-1]
    assert isinstance(failed, FailedRolledBack) and failed.reason == "values_do_not_cast"
    assert failed.detail["first"] == [{"row": 2, "column": "px", "value": "twelve"}]


def test_a_date_that_does_not_parse_fails_the_file_and_a_good_one_is_iso(world):
    approved_transformation(world.session, world.feed, "trade_date",
                            [{"op": "parse_date", "format": "DD-MM-YYYY"}, {"op": "cast", "type": "date"}])
    good = run(world, HEADER + "a,02-11-2026,1,1\n")
    assert good.load.status == "loaded"
    assert records(world, good.load)[0].values["price_date"] == "2026-11-02"
    bad = run(world, HEADER + "a,2026-11-02,1,1\n")  # the wrong format for DD-MM-YYYY
    failed = bad.stages[-1]
    assert failed.reason == "values_do_not_cast"
    assert failed.detail["first"] == [{"row": 1, "column": "trade_date", "value": "2026-11-02"}]
    assert bad.load.status == "failed_rolled_back"
    assert StandardRecordRepository(world.session).count(bad.load.id) == 0


def test_a_rename_becomes_the_label_and_the_standard_record_keeps_the_element_key(world):
    approved_transformation(world.session, world.feed, "px", [{"op": "rename", "to": "last_price"}])
    result = run(world, HEADER + "a,2026-11-02,1,2.5\n")
    assert result.load.status == "loaded"
    assert "close_price" in records(world, result.load)[0].values
    detail = result.stages[3].detail
    assert detail["labels"] == {"close_price": "last_price"}
    assert detail["transformations"] == "approved"


def test_no_transformation_leaves_the_pass_through_detail_unchanged(world):
    assert run(world, HEADER + "a,2026-11-02,1,2.5\n").stages[3].detail == {"mastering": "pass_through",
                                                                           "transformations": "none"}


DUPLICATED = HEADER + "k1,2026-11-02,1,1\nk2,2026-11-02,2,2\nk1,2026-11-02,3,3\n"  # rows 1 and 3 share a key


def set_policy(world, policy: str):
    KeyPolicyRepository(world.session).set(world.feed, policy, ["instr_id"], "dev-data-steward", "Synthetic reason.")


def test_hold_fails_the_load_on_a_duplicated_key_and_writes_nothing(world):
    set_policy(world, "hold")
    result = run(world, DUPLICATED)
    failed = result.stages[-1]
    assert isinstance(failed, FailedRolledBack) and (failed.stage, failed.reason) == ("curated", "duplicate_keys")
    assert failed.detail["count"] == 1 and failed.detail["first"] == [{"instrument_id": "k1"}]
    assert "1" in failed.detail["message"] and "k1" in failed.detail["message"]
    assert (result.load.status, result.load.failed_stage) == ("failed_rolled_back", "curated")
    assert StandardRecordRepository(world.session).count(result.load.id) == 0


def test_hold_lists_at_most_five_keys(world):
    set_policy(world, "hold")
    rows = "".join(f"k{n},2026-11-02,1,1\nk{n},2026-11-02,2,2\n" for n in range(8))
    failed = run(world, HEADER + rows).stages[-1]
    assert failed.detail["count"] == 8 and len(failed.detail["first"]) == 5


def test_keep_last_keeps_the_later_row_and_lists_the_dropped_one(world):
    set_policy(world, "keep_last")
    result = run(world, DUPLICATED)
    assert result.load.status == "loaded"
    assert [(r.row_number, r.values["instrument_id"]) for r in records(world, result.load)] == [(2, "k2"), (3, "k1")]
    curated = result.stages[1]
    assert curated.detail["duplicates_dropped"] == {"count": 1, "rows": [1]}
    assert (curated.rows_in, curated.rows_out) == (3, 2)
    assert [(s.stage, s.rows_in, s.rows_out) for s in LoadRepository(world.session).stages(result.load.id)] == [
        ("raw", 3, 3), ("curated", 3, 2), ("validated", 2, 2), ("standard", 2, 2)]


def test_flag_flags_every_row_of_the_key_and_the_trust_score_falls(world):
    set_policy(world, "flag")
    result = run(world, DUPLICATED)
    assert result.load.status == "loaded"
    flags = {r.row_number: r.flags for r in records(world, result.load)}
    assert flags == {1: ["duplicate_key"], 2: [], 3: ["duplicate_key"]}
    validated = result.stages[2]
    assert validated.detail["rules"] == {"duplicate_key": 2} and validated.detail["duplicates_flagged"] == 2
    flagged = StandardRecordRepository(world.session).count_flagged(result.load.id)
    assert flagged == 2 and trust_score(3, flagged) < 100


def test_no_policy_and_no_key_columns_means_no_duplicate_check(world):
    result = run(world, DUPLICATED)
    assert result.load.status == "loaded" and all(r.flags == [] for r in records(world, result.load))
    assert result.stages[1].detail.get("duplicates_dropped") is None


def test_a_key_policy_whose_key_is_not_mapped_is_ignored_and_says_so(world):
    KeyPolicyRepository(world.session).set(world.feed, "hold", ["no_such_column"], "dev-data-steward", "Synthetic.")
    result = run(world, DUPLICATED)
    assert result.load.status == "loaded"
    assert result.stages[1].detail["key_columns_ignored"] == ["no_such_column"]


def test_a_scheduled_load_runs_only_the_transformations_the_latest_go_live_pinned(world):
    approved_transformation(world.session, world.feed, "px", [{"op": "placeholder_to_null", "values": ["n/a"]}])
    live = go_live(world.session, world.feed)
    assert len(live.transformation_ids) == 1
    ok = run(world, HEADER + "a,2026-11-02,1,N/A\n", purpose="scheduled", go_live_id=live.id)
    assert ok.load.status == "loaded"
    approved_transformation(world.session, world.feed, "px",
                            [{"op": "placeholder_to_null", "values": ["n/a", "-"]}])  # not approved by a go-live
    changed = run(world, HEADER + "a,2026-11-03,1,N/A\n", purpose="scheduled", go_live_id=live.id)
    failed = changed.stages[-1]
    assert (failed.stage, failed.reason) == ("raw", "configuration_changed_since_go_live")
    assert GoLiveRepository(world.session).latest(world.feed).id == live.id


# ---------------------------------------------------------------- the security master, end to end (D8)


def register_with_keys(session, feed_id, key_columns: list[str]) -> None:
    """tests/onboarded.py's chain, with the approved profile summary naming its key columns (task 3.2)."""
    from core.proposal_log import ProposalLog
    from ports.llm.base import LLMResult
    from tests.onboarded import _Summary

    profile = profiled(session, feed_id)

    result = LLMResult(output=_Summary(recommended_outcome="meets"), provider="test", model="test", model_version="0",
                       prompt_id="profiler", prompt_version="v1", latency_ms=0, raw_ref=None)
    entry = ProposalLog(session).record(agent_name="ProfilerAgent", input_ref="test://profile", result=result)
    keyed = ProposalRepository(session).create(
        proposal_log_id=entry.id, kind="profile_summary", autonomy="A2", required_approver_roles=["backend_team"],
        feed_id=feed_id, payload={"profile_id": str(profile.id), "summary": "Synthetic summary.",
                                  "recommended_outcome": "meets", "gaps": [], "reasons": [],
                                  "key_columns": key_columns})
    ProposalRepository(session).record_decision(keyed.id, user=dev_user_for("backend_team"), decision="approved")
    signoff = FeedSignoffRepository(session).add_version(
        feed_id=feed_id, profile_id=profile.id, proposal_id=keyed.id, outcome="meets", gaps=[], reasons=[],
        signed_off_by=BACKEND)
    FeedRegistrationRepository(session).add_version(
        feed_id=feed_id, signoff_id=signoff.id, owner_id="dev-data-owner", licence="Synthetic licence",
        permitted_use="Internal analysis", redistribution="Not allowed", visibility="open", registered_by=BACKEND)
    FeedRepository(session).register(feed_id, owner_id="dev-data-owner")


@pytest.fixture
def master(db_session, glossary, tmp_path):
    feed = FeedRepository(db_session).create_candidate(code="syn_security_master", name="Security master")
    register_with_keys(db_session, feed.id, ["TICKER_NAME"])
    for field, key, kind in (("TICKER_NAME", "sec_ticker_name", "string"), ("COUNTRY", "sec_country", "string"),
                             ("RECORD_DATE", "sec_record_date", "date"), ("BEST_EPS", "sec_best_eps", "decimal")):
        approved_element(db_session, new_element(key, data_type=kind), feed_id=feed.id, column=field)
        approved_mapping(db_session, feed.id, field, key)
    approved_transformation(db_session, feed.id, "COUNTRY", [{"op": "placeholder_to_null", "values": ["ALL", "NA"]}])
    approved_transformation(db_session, feed.id, "RECORD_DATE",
                            [{"op": "parse_date", "format": "DD-MM-YYYY"}, {"op": "cast", "type": "date"}])
    table, _ = read_xlsx_bytes(DEMO.read_bytes(), original_name=DEMO.name)  # the intake's reading of the D8 file
    blobs = LocalBlobStore(tmp_path / "blob")
    blobs.put(KEY, canonical_csv(table))
    return SimpleNamespace(feed=feed.id, blobs=blobs, session=db_session)


def load_master(master):
    return run_engine(master.session, master.blobs, master.feed, file_ref=KEY, purpose="go_live_test")


def test_the_security_master_loads_with_iso_dates_null_countries_and_the_duplicate_ticker_flagged(master):
    result = load_master(master)
    assert result.load.status == "loaded", [s.detail for s in result.stages]
    rows = StandardRecordRepository(master.session).list_for_load(result.load.id, limit=1000)
    assert len(rows) == 70
    assert {r.values["sec_record_date"] for r in rows} == {"2019-12-31"}
    assert {r.values["sec_country"] for r in rows} == {None}
    assert rows[0].values["sec_best_eps"] == "473.7140000000"
    flagged = {r.row_number for r in rows if r.flags}
    assert flagged == {1, 2} and rows[0].flags == ["duplicate_key"]
    assert StandardRecordRepository(master.session).count_flagged(result.load.id) == 2


def test_the_security_master_under_keep_last_has_69_records(master):
    KeyPolicyRepository(master.session).set(master.feed, "keep_last", ["TICKER_NAME"], "dev-data-steward", "Synthetic.")
    result = load_master(master)
    assert result.load.status == "loaded"
    assert StandardRecordRepository(master.session).count(result.load.id) == 69
    assert result.stages[1].detail["duplicates_dropped"] == {"count": 1, "rows": [1]}


def test_the_security_master_under_hold_fails(master):
    KeyPolicyRepository(master.session).set(master.feed, "hold", ["TICKER_NAME"], "dev-data-steward", "Synthetic.")
    result = load_master(master)
    assert result.load.status == "failed_rolled_back"
    assert result.stages[-1].reason == "duplicate_keys" and "100001 SY Equity" in result.stages[-1].detail["message"]
    assert StandardRecordRepository(master.session).count(result.load.id) == 0
