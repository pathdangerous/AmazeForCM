"""DuckDB profiling of one CSV file (phase 4 plan, assumptions 6 and 7).

profile_csv(path) opens an in-memory DuckDB connection, reads the file with a header and with types
detected from every row (sample_size=-1, so the same file always gives the same types), and returns the
row count and, per column in file order, its type, null count and rate, and exact distinct count. An
empty cell is a null. The connection is closed afterwards; no DuckDB database file is kept.

Column names come from outside: each is used only as a quoted identifier with its quotes doubled, and
the path is a bound parameter. No model call and no database session here (rule 2 does not apply, but
the step that calls this holds no connection either: assumption 11).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from core.domain.profiling import FileColumn, FileProfile

ENGINE = f"duckdb {duckdb.__version__}"
_READ = "read_csv($path, header = true, sample_size = -1)"


class NotATable(ValueError):
    """DuckDB could not read the file as a CSV table (step 02: spec_only_low_confidence)."""


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def profile_csv(path: Path) -> FileProfile:
    params = {"path": str(path)}
    connection = duckdb.connect(":memory:")
    try:
        try:
            described = connection.execute(f"DESCRIBE SELECT * FROM {_READ}", params).fetchall()
            names = [row[0] for row in described]
            parts = ["count(*)"]
            for name in names:
                parts += [f"count({_quoted(name)})", f"count(DISTINCT {_quoted(name)})"]
            counts = connection.execute(f"SELECT {', '.join(parts)} FROM {_READ}", params).fetchone()
        except duckdb.Error as exc:
            raise NotATable(" ".join(str(exc).splitlines()[:2])) from None  # DuckDB's own first two lines
    finally:
        connection.close()
    rows = counts[0]
    columns = []
    for i, (name, type_) in enumerate((row[0], row[1]) for row in described):
        non_null, distinct = counts[1 + 2 * i], counts[2 + 2 * i]
        nulls = rows - non_null
        columns.append(FileColumn(name=name, type=type_, null_count=nulls,
                                  null_rate=round(nulls / rows, 6) if rows else None, distinct_count=distinct))
    return FileProfile(row_count=rows, columns=columns, engine=ENGINE)
