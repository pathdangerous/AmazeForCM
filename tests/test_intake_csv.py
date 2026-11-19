"""Task 2.1a: the CSV reader, the intake model, the canonical CSV and the manifest. Synthetic bytes only."""

from __future__ import annotations

import json
import time

import duckdb
import pytest

from core.intake import (
    NotATable,
    SkippedRow,
    canonical_csv,
    clean_header,
    manifest_json,
    read_csv_bytes,
)


def read(data: bytes, **kw):
    return read_csv_bytes(data, original_name="t.csv", **kw)


def test_comma_utf8():
    table, m = read(b"id,name\n1,Ann\n2,Bob\n")
    assert table.header == ["id", "name"]
    assert table.rows == [["1", "Ann"], ["2", "Bob"]]
    assert (m.encoding, m.delimiter, m.header_row, m.row_count) == ("utf-8", ",", 1, 2)


def test_semicolon_cp1252_accented_header():
    data = "id;Société\n1;Café\n2;Thé\n".encode("cp1252")
    table, m = read(data)
    assert table.header == ["id", "Société"]
    assert table.rows[0] == ["1", "Café"]
    assert (m.encoding, m.delimiter) == ("cp1252", ";")


def test_tab():
    table, m = read(b"a\tb\tc\n1\t2\t3\n")
    assert m.delimiter == "\t" and table.rows == [["1", "2", "3"]]


def test_pipe():
    table, m = read(b"a|b\n1|2\n3|4\n")
    assert m.delimiter == "|" and len(table.rows) == 2


def test_utf8_bom():
    table, m = read(b"\xef\xbb\xbfid,name\n1,Ann\n")
    assert table.header == ["id", "name"] and m.encoding == "utf-8-sig"


def test_utf16_le_bom():
    data = b"\xff\xfe" + "id,name\n1,Ann\n".encode("utf-16-le")
    table, m = read(data)
    assert table.header == ["id", "name"] and table.rows == [["1", "Ann"]] and m.encoding == "utf-16-le"


def test_utf16_be_bom():
    data = b"\xfe\xff" + "id,name\n1,Ann\n".encode("utf-16-be")
    table, m = read(data)
    assert table.header == ["id", "name"] and m.encoding == "utf-16-be"


def test_title_lines_and_blank_before_header():
    table, m = read(b"Monthly report\nSynthetic\n\nid,name,qty\n1,Ann,5\n2,Bob,6\n")
    assert table.header_row == 4 and m.header_row == 4
    assert table.skipped == [SkippedRow(row=n, reason="before_header") for n in (1, 2, 3)]
    assert m.row_count == 2


def test_blank_line_and_repeated_header_between_data():
    table, _ = read(b"id,name\n1,Ann\n\n2,Bob\nid,name\n3,Cy\n")
    assert [r[0] for r in table.rows] == ["1", "2", "3"]
    assert table.skipped == [SkippedRow(row=3, reason="empty"), SkippedRow(row=5, reason="repeated_header")]


def test_short_row_padded():
    table, _ = read(b"a,b,c\n1,2\n")
    assert table.rows == [["1", "2", None]]


def test_long_row_with_extras_refused_and_empty_extras_accepted():
    with pytest.raises(NotATable) as e:
        read(b"a,b\n1,2\n3,4,5\n")
    assert "row 3" in e.value.detail
    table, _ = read(b"a,b\n1,2\n3,4,\n")
    assert table.rows[1] == ["3", "4"]


def test_quoted_delimiter_and_newline():
    table, _ = read(b'id,note\n1,"a,b"\n2,"line1\nline2"\n3,x\n')
    assert table.rows == [["1", "a,b"], ["2", "line1\nline2"], ["3", "x"]]


def test_empty_and_duplicate_header_names_cleaned():
    table, m = read(b"a,,a,a\n1,2,3,4\n", header_row=1)
    assert table.header == ["a", "column_2", "a_2", "a_3"]
    assert [c.name for c in m.columns] == table.header
    assert clean_header([" x ", "x", ""]) == ["x", "x_2", "column_3"]


def test_empty_header_name_detected_without_operator_help():
    table, _ = read(b"a,,c\n1,2,3\n4,5,6\n")
    assert table.header == ["a", "column_2", "c"] and table.header_row == 1


def test_whitespace_cell_kept_and_empty_cell_none():
    table, m = read(b"a,b,c\n 1,, \n")
    assert table.rows == [[" 1", None, " "]]
    assert m.columns[1].cell_types == {"empty": 1}
    assert m.columns[2].cell_types == {"text": 1}


def test_nul_bytes_and_empty_file():
    with pytest.raises(NotATable) as e:
        read(b"a,b\n1,\x002\n")
    assert e.value.detail == "binary"
    with pytest.raises(NotATable):
        read(b"")


def test_header_row_given_overrides_detection():
    data = b"id,name\n1,Ann\nx,y\n"
    table, _ = read(data, header_row=3)
    assert table.header == ["x", "y"] and table.rows == []
    assert [s.reason for s in table.skipped] == ["before_header"] * 2
    with pytest.raises(NotATable):
        read(data, header_row=9)


def test_one_column_file():
    table, m = read(b"name\nAnn\nBob\n")
    assert m.delimiter == "," and table.header == ["name"] and len(table.rows) == 2


def test_canonical_round_trip_through_duckdb(tmp_path):
    data = "Société;note;n\r\nA;\"x;y\";1\r\nB;;2\r\n\r\nC;\"l1\nl2\";3\r\n".encode("cp1252")
    table, _ = read(data)
    out = canonical_csv(table)
    assert not out.startswith(b"\xef\xbb\xbf") and b"\r" not in out and out.startswith("Société,note,n\n".encode())
    path = tmp_path / "c.csv"
    path.write_bytes(out)
    con = duckdb.connect(":memory:")
    rel = con.execute("SELECT * FROM read_csv($path, header = true, sample_size = -1)", {"path": str(path)})
    names = [d[0] for d in rel.description]
    assert names == table.header
    assert len(rel.fetchall()) == len(table.rows) == 3


def test_manifest_json_fields():
    _, m = read(b"id;name\n1;Ann\n2;\n")
    doc = json.loads(manifest_json(m))
    assert doc["format"] == "csv" and doc["encoding"] == "utf-8" and doc["delimiter"] == ";"
    assert doc["header_row"] == 1 and doc["row_count"] == 2 and doc["reader"] == "csv stdlib"
    assert doc["columns"][1] == {"name": "name", "position": 2, "cell_types": {"text": 1, "empty": 1}}
    assert doc["extraction"] == "parsed" and doc["pages"] == [] and doc["sheet"] is None
    raw = manifest_json(m).decode()
    assert list(json.loads(raw)) == sorted(json.loads(raw))


def test_large_file_fast():
    header = ",".join(f"c{i}" for i in range(10))
    lines = [header] + [",".join(f"v{r}_{c}" for c in range(10)) for r in range(100_000)]
    data = ("\n".join(lines) + "\n").encode()
    start = time.perf_counter()
    table, m = read_csv_bytes(data, original_name="big.csv")
    out = canonical_csv(table)
    elapsed = time.perf_counter() - start
    assert m.row_count == 100_000 and out.count(b"\n") == 100_001
    assert elapsed < 10, elapsed
