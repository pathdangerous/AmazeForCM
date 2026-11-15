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
