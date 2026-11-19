"""Task 2.1b: the XLSX reader into the intake model. Workbooks are made in the tests (synthetic)."""

from __future__ import annotations

import datetime as dt
import io
import time
from pathlib import Path

import duckdb
import openpyxl
import pytest

from core.intake import (
    NotATable,
    SkippedRow,
    canonical_csv,
    list_sheets,
    manifest_json,
    read_xlsx_bytes,
)

DEMO = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic" / "Synthetic_Security_Master.xlsx"


def book(build) -> bytes:
    wb = openpyxl.Workbook()
    build(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def read(data: bytes, **kw):
    return read_xlsx_bytes(data, original_name="t.xlsx", **kw)


def two_sheets(wb):
    a = wb.active
    a.title = "First"
    a.append(["id", "name"])
    a.append([1, "Ann"])
    b = wb.create_sheet("Second")
    b.append(["code", "label"])
    b.append(["x", "y"])
    b.append(["z", "w"])


def test_sheet_choice_and_list_sheets():
    data = book(two_sheets)
    assert list_sheets(data) == ["First", "Second"]
    table, m = read(data)
    assert table.header == ["id", "name"] and m.sheet == "First" and m.sheets == ["First", "Second"]
    table, m = read(data, sheet="Second")
    assert table.header == ["code", "label"] and table.rows == [["x", "y"], ["z", "w"]] and m.sheet == "Second"
    with pytest.raises(NotATable) as e:
        read(data, sheet="Nope")
    assert "Nope" in e.value.detail


def test_title_row_merged_and_blank_row_before_header():
    def build(wb):
        ws = wb.active
        ws["A1"] = "Synthetic report"
        ws.merge_cells("A1:D1")
        ws["A3"], ws["B3"], ws["C3"], ws["D3"] = "id", "name", "qty", "price"
        ws.append([1, "Ann", 5, 1.5])
        ws.append([2, "Bob", 6, 2.5])

    table, m = read(book(build))
    assert table.header_row == 3 and m.header_row == 3
    assert table.skipped == [SkippedRow(row=1, reason="merged"), SkippedRow(row=2, reason="empty")]
    assert table.rows == [["1", "Ann", "5", "1.5"], ["2", "Bob", "6", "2.5"]]
    assert m.row_count == 2


def test_section_banner_between_data_rows_skipped_and_vertical_merge_kept():
    def build(wb):
        ws = wb.active
        ws.append(["id", "name", "grp"])
        ws.append([1, "Ann", "g1"])
        ws.append(["Section B"])
        ws.merge_cells("A3:C3")
        ws.append([2, "Bob", "g2"])
        ws.append([3, "Cy", None])
        ws.merge_cells("C4:C5")  # vertical-only merge: rows stay; value only in the top-left cell

    table, _ = read(book(build))
    assert table.skipped == [SkippedRow(row=3, reason="merged")]
    assert table.rows == [["1", "Ann", "g1"], ["2", "Bob", "g2"], ["3", "Cy", None]]


def test_types_and_cell_type_counts():
    def build(wb):
        ws = wb.active
        ws.append(["i", "f", "g", "h", "b", "d", "dt", "n"])
        ws.append([5, 70.0, 0.1 + 0.2, 2.5, True, dt.date(2019, 12, 31), dt.datetime(2019, 12, 31, 8, 30), None])
        ws.append([6, 71.0, 1.0, 3.5, False, dt.datetime(2020, 1, 1), dt.datetime(2020, 1, 1, 0, 0, 1), None])

    table, m = read(book(build))
    r1, r2 = table.rows
    assert r1[:2] == ["5", "70"]  # an integral float is "70", not "70.0"
    # openpyxl stores a float with 16 significant digits, so 0.1 + 0.2 is saved (and read back) as 0.3; the reader
    # gives repr() of the float it is handed, never "0.30000000000000004" noise it did not receive.
    assert r1[2] == "0.3"
    assert r1[3] == "2.5" and r1[4:] == ["true", "2019-12-31", "2019-12-31T08:30:00", None]
    assert r2[4:] == ["false", "2020-01-01", "2020-01-01T00:00:01", None]
    types = {c.name: c.cell_types for c in m.columns}
    assert types["i"] == {"number": 2} and types["f"] == {"number": 2}
    assert types["b"] == {"bool": 2}
    assert types["d"] == {"date": 2}
    assert types["dt"] == {"datetime": 2}
    assert types["n"] == {"empty": 2}


def test_text_that_looks_like_a_date_stays_text():
    def build(wb):
        ws = wb.active
        ws.append(["when", "n"])
        ws.append(["31-12-2019", 1])

    table, m = read(book(build))
    assert table.rows == [["31-12-2019", "1"]]
    assert m.columns[0].cell_types == {"text": 1}


def test_text_kept_as_is_and_empty_string_is_empty():
    def build(wb):
        ws = wb.active
        ws.append(["a", "b"])
        ws.append([" x ", ""])
        ws.append(["y", "z"])

    table, m = read(book(build))
    assert table.rows[0] == [" x ", None]
    assert m.columns[1].cell_types == {"empty": 1, "text": 1}


def test_formula_cell_has_no_cached_value_when_written_by_openpyxl():
    # openpyxl does not compute formulas: a workbook it wrote has no cached value, so data_only gives None.
    # (A workbook saved by a spreadsheet program carries the cached value and is read as that value.)
    def build(wb):
        ws = wb.active
        ws.append(["a", "b", "c"])
        ws.append([1, 2, "=A2+B2"])
        ws.append([3, 4, 5])

    table, m = read(book(build))
    assert table.rows[0] == ["1", "2", None]
    assert m.columns[2].cell_types == {"empty": 1, "number": 1}


@pytest.mark.parametrize("data", [b"\x00\x01random bytes\xff" * 20, b"id,name\n1,Ann\n", b""])
def test_not_an_xlsx(data):
    with pytest.raises(NotATable) as e:
        read(data)
    assert e.value.detail == "not a readable .xlsx workbook"
    with pytest.raises(NotATable):
        list_sheets(data)


def test_trailing_empty_columns_dropped_and_manifest_fields():
    def build(wb):
        ws = wb.active
        ws.append(["a", "b", None, None])
        ws.append([1, 2, None, ""])
        ws.cell(row=5, column=6).value = None

    table, m = read(book(build), )
    assert table.header == ["a", "b"] and table.rows == [["1", "2"]]
    assert (m.format, m.encoding, m.delimiter) == ("xlsx", None, None)
    assert m.reader == f"openpyxl {openpyxl.__version__}"
    assert m.original_name == "t.xlsx"


def test_header_row_given_is_used_as_is():
    def build(wb):
        ws = wb.active
        ws.append(["id", "name"])
        ws.append([1, "Ann"])
        ws.append(["x", "y"])
        ws.append(["p", "q"])

    table, _ = read(book(build), header_row=3)
    assert table.header == ["x", "y"] and table.rows == [["p", "q"]]
    assert [s.reason for s in table.skipped] == ["before_header"] * 2
    with pytest.raises(NotATable):
        read(book(build), header_row=99)


def test_canonical_round_trip_through_duckdb(tmp_path):
    def build(wb):
        ws = wb.active
        ws.append(["Société", "note", "n", "when"])
        ws.append(["A", "x,y", 1, dt.date(2020, 2, 3)])
        ws.append(["B", None, 2, dt.date(2020, 2, 4)])
        ws.append(["C", "l1\nl2", 3, None])

    table, m = read(book(build))
    out = canonical_csv(table)
    path = tmp_path / "c.csv"
    path.write_bytes(out)
    con = duckdb.connect(":memory:")
    rel = con.execute("SELECT * FROM read_csv($path, header = true, sample_size = -1)", {"path": str(path)})
    assert [d[0] for d in rel.description] == table.header
    assert len(rel.fetchall()) == 3
    assert '"format": "xlsx"' in manifest_json(m).decode()


def test_demo_file():  # the demo file is committed (phase 7.8, D8): never skipped
    data = DEMO.read_bytes()
    table, m = read(data)
    assert m.sheet == "SYN_Security_Master" and m.header_row == 1
    assert m.row_count == 70 and len(table.rows) == 70
    assert len(table.header) == 30 and len(m.columns) == 30
    assert table.header[0] == "SOURCE" and table.header[-1] == "BEST_EPS_1_WEEK_PERCENT_CHANGE"
    cols = {c.name: c for c in m.columns}
    assert cols["RECORD_DATE"].cell_types == {"text": 70}
    assert cols["BEST_EPS_1_WEEK_PERCENT_CHANGE"].cell_types == {"empty": 70}
    idx = table.header.index("TICKER_NAME")
    assert [r[idx] for r in table.rows].count("100001 SY Equity") == 2


def test_large_workbook_fast():
    # Phase 7.8, test-time ceiling (old -> new, recorded in the progress file): 20,000 rows in 20 s -> 5,000 rows in
    # 10 s. openpyxl itself reads about 40,000 cells a second here, so 20,000 rows cost 10 s of the suite; the
    # developer's 100,000-row requirement is the CSV test (tests/test_s00_intake.py). Written in write-only mode:
    # building the workbook is not what is timed.
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet()
    ws.append([f"c{i}" for i in range(10)])
    for r in range(5_000):
        ws.append([f"v{r}_{c}" if c % 2 else r * c for c in range(10)])
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    start = time.perf_counter()
    table, m = read(data)
    elapsed = time.perf_counter() - start
    assert m.row_count == 5_000 and len(table.header) == 10
    assert elapsed < 10, elapsed
