"""DuckDB profiling of one CSV file (phase 4 plan, assumptions 6, 7 and 9; task 1).

profile_csv() reads a local file with an in-memory DuckDB and returns the file's statistics. The step
(s02_profile, task 4) gets the file through BlobStore.get(); these tests give it a file directly.
"""

from __future__ import annotations

import duckdb
import pytest

from core.steps.profiling import NotATable, profile_csv


def write(tmp_path, name: str, text: str | bytes):
    path = tmp_path / name
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


LIFECYCLE = (
    "instr_id,issued_on,status,lot\n"
    "syn-0001,2020-01-02,active,100\n"
    "syn-0002,,active,100\n"
    "syn-0003,2021-05-06,,10\n"
    "syn-0004,2021-05-06,matured,\n"
)


def test_columns_are_in_file_order_with_duckdbs_types(tmp_path):
    profile = profile_csv(write(tmp_path, "a.csv", LIFECYCLE))
    assert [(c.name, c.type) for c in profile.columns] == [
        ("instr_id", "VARCHAR"), ("issued_on", "DATE"), ("status", "VARCHAR"), ("lot", "BIGINT")]
    assert profile.row_count == 4


def test_an_empty_cell_is_a_null_and_the_null_rate_is_nulls_over_rows(tmp_path):
    columns = {c.name: c for c in profile_csv(write(tmp_path, "a.csv", LIFECYCLE)).columns}
    assert [(n, columns[n].null_count, columns[n].null_rate) for n in ("instr_id", "issued_on", "status", "lot")] == [
        ("instr_id", 0, 0.0), ("issued_on", 1, 0.25), ("status", 1, 0.25), ("lot", 1, 0.25)]


def test_the_distinct_count_is_exact_and_leaves_out_nulls(tmp_path):
    columns = {c.name: c for c in profile_csv(write(tmp_path, "a.csv", LIFECYCLE)).columns}
    assert {n: c.distinct_count for n, c in columns.items()} == {
        "instr_id": 4, "issued_on": 2, "status": 2, "lot": 2}


def test_types_are_detected_from_every_row_not_a_sample(tmp_path):
    """DuckDB samples 20480 rows by default; one text value after that must still make the column text."""
    rows = "".join(f"{i}\n" for i in range(25_000))
    profile = profile_csv(write(tmp_path, "long.csv", "n\n" + rows + "not a number\n"))
    assert [(c.name, c.type) for c in profile.columns] == [("n", "VARCHAR")]
    assert profile.row_count == 25_001


def test_the_same_file_twice_gives_the_same_profile(tmp_path):
    path = write(tmp_path, "a.csv", LIFECYCLE)
    assert profile_csv(path) == profile_csv(path)


def test_a_hostile_header_is_only_a_column_name(tmp_path):
    hostile = 'x"") FROM t; DROP TABLE t; --'
    text = f'"{hostile}",plain\n1,2\n3,\n'
    profile = profile_csv(write(tmp_path, "h.csv", text))
    assert [(c.name, c.null_count, c.distinct_count) for c in profile.columns] == [
        ('x") FROM t; DROP TABLE t; --', 0, 2), ("plain", 1, 1)]


def test_a_header_only_file_has_its_columns_and_no_rows(tmp_path):
    profile = profile_csv(write(tmp_path, "h.csv", "instr_id,mic,lot\n"))
    assert profile.row_count == 0
    assert [c.name for c in profile.columns] == ["instr_id", "mic", "lot"]
    assert all(c.null_rate is None and c.distinct_count == 0 for c in profile.columns)


def test_a_file_that_is_not_a_csv_table_is_refused_by_name(tmp_path):
    with pytest.raises(NotATable, match="utf-8"):
        profile_csv(write(tmp_path, "b.csv", b"\x00\x01\x02\xff\xfe binary"))


def test_the_engine_is_the_pinned_duckdb(tmp_path):
    assert duckdb.__version__ == "1.5.6"
    assert profile_csv(write(tmp_path, "a.csv", LIFECYCLE)).engine == "duckdb 1.5.6"


# ---------------------------------------------------------------- task 2: the spec and coverage


import uuid  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

from core.domain.requirement import Requirement  # noqa: E402
from core.steps.profiling import coverage_of, spec_of  # noqa: E402
from ports.config import REPO_ROOT  # noqa: E402
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue  # noqa: E402

CATALOGUE = SourceCatalogue.load(DEFAULT_CATALOGUE)
FILES = REPO_ROOT / "fixtures" / "sources"


def requirement(elements=(), terms=()):
    return Requirement(id=uuid.uuid4(), created_at=datetime.now(UTC), requester_id="dev-requester", title="t",
                       description="", status="onboarding", elements=sorted(elements), terms=sorted(terms))


def items(coverage):
    return [(i.item, i.kind, i.declared, i.column, i.in_file, i.null_rate, i.distinct_count) for i in coverage.items]


def test_coverage_links_each_requested_element_to_its_column_and_its_statistics():
    entry = CATALOGUE.get("syn_instrument_lifecycle")
    file = profile_csv(FILES / entry.source_ref)
    req = requirement(["issue_date", "instrument_id", "maturity_date"])
    coverage = coverage_of(req, entry, file)
    assert coverage.requirement_id == req.id
    assert items(coverage) == [
        ("instrument_id", "element", True, "instr_id", True, 0.0, 10),
        ("issue_date", "element", True, "issued_on", True, 0.2, 7),
        ("maturity_date", "element", False, None, False, None, None),
    ]
    assert coverage.missing == ["maturity_date"]


def test_a_ticked_term_is_covered_by_an_other_field_column():
    entry = CATALOGUE.get("syn_open_esg_scores")
    coverage = coverage_of(requirement(terms=["ESG score"]), entry, profile_csv(FILES / entry.source_ref))
    assert items(coverage) == [("ESG score", "term", True, "esg_score", True, 0.0, 10)]
    assert coverage.missing == []


def test_a_declared_column_absent_from_the_file_is_missing(tmp_path):
    entry = CATALOGUE.get("syn_exchange_volumes")
    file = profile_csv(write(tmp_path, "v.csv", "instr_id,trade_date\nsyn-0001,2026-11-02\n"))
    coverage = coverage_of(requirement(["volume"]), entry, file)
    assert items(coverage) == [("volume", "element", True, "traded_qty", False, None, None)]
    assert coverage.missing == ["volume"]


def test_without_a_file_coverage_comes_from_the_spec_alone():
    entry = CATALOGUE.get("syn_listing_details")
    coverage = coverage_of(requirement(["lot_size", "isin"]), entry, None)
    assert items(coverage) == [("isin", "element", False, None, None, None, None),
                               ("lot_size", "element", True, "board_lot", None, None, None)]
    assert coverage.missing == ["isin"]


def test_a_header_only_file_has_the_columns_but_no_statistics():
    entry = CATALOGUE.get("syn_listing_details")
    coverage = coverage_of(requirement(["lot_size"]), entry, profile_csv(FILES / entry.source_ref))
    assert items(coverage) == [("lot_size", "element", True, "board_lot", True, None, 0)]


def test_the_spec_is_kept_as_it_was_read():
    entry = CATALOGUE.get("syn_exchange_volumes")
    assert spec_of(entry).model_dump() == {
        "source_code": "syn_exchange_volumes", "elements": ["instrument_id", "price_date", "volume"],
        "other_fields": [], "columns": {"instr_id": "instrument_id", "trade_date": "price_date",
                                        "traded_qty": "volume"}}
