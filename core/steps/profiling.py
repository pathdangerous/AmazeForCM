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

import re
from pathlib import Path

import duckdb

from core.domain.profiling import (
    DuplicateKey,
    FileColumn,
    FileProfile,
    IncompleteRecords,
    ItemCoverage,
    ProfileIssue,
    RequirementCoverage,
    SourceSpec,
    issues_of,
)
from core.domain.requirement import Requirement
from ports.connectors.catalogue import SourceEntry

ENGINE = f"duckdb {duckdb.__version__}"
_READ = "read_csv($path, header = true, sample_size = -1)"
_READ_TEXT = "read_csv($path, header = true, sample_size = -1, all_varchar = true)"


class NotATable(ValueError):
    """DuckDB could not read the file as a CSV table (step 02: spec_only_low_confidence)."""


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


PLACEHOLDERS = ("ALL", "NA", "N/A", "-", "")  # compared trimmed and upper-cased; a null is not one
SAMPLE_COUNT, SAMPLE_WIDTH = 5, 64

# Text date formats, in the order they are tried: (name, strptime format, whole-value pattern). A pattern keeps
# strptime from accepting "1-2-2020"; strptime keeps 32-13-2019 out.
_DATE_FORMATS = (
    ("DD-MM-YYYY", "%d-%m-%Y", r"\d{2}-\d{2}-\d{4}"),
    ("MM-DD-YYYY", "%m-%d-%Y", r"\d{2}-\d{2}-\d{4}"),
    ("DD/MM/YYYY", "%d/%m/%Y", r"\d{2}/\d{2}/\d{4}"),
    ("MM/DD/YYYY", "%m/%d/%Y", r"\d{2}/\d{2}/\d{4}"),
    ("YYYY-MM-DD", "%Y-%m-%d", r"\d{4}-\d{2}-\d{2}"),
    ("YYYYMMDD", "%Y%m%d", r"\d{8}"),
    ("DD.MM.YYYY", "%d.%m.%Y", r"\d{2}\.\d{2}\.\d{4}"),
)
_MONTH_FIRST_TWIN = {"DD-MM-YYYY": "MM-DD-YYYY", "DD/MM/YYYY": "MM/DD/YYYY"}


def _placeholder(name: str) -> str:
    return f"upper(trim({_quoted(name)})) IN ({', '.join(repr(p) for p in PLACEHOLDERS)})"


def _text_date_format(connection, name: str, samples: list[str], typed_text: bool) -> tuple[str | None, bool]:
    """The first format every real value fits, and whether day-first was chosen though month-first fit too.
    Runs on the raw text (table r, read with all_varchar), whatever type DuckDB's sniffer gave the column.
    ISO YYYY-MM-DD is how the intake writes real dates, so it counts only in a column DuckDB left as text.
    A format any sample does not even look like is dropped without touching the table; the rest are checked
    on the column's distinct values."""
    column = _quoted(name)
    formats = [f for f in _DATE_FORMATS if typed_text or f[0] != "YYYY-MM-DD"]
    tried = [f for f in formats if all(re.fullmatch(f[2], s) for s in samples)] if samples else []
    if not tried:
        return None, False
    bad = [f"count(*) FILTER (WHERE NOT (regexp_full_match(v, '{pattern}') AND try_strptime(v, '{fmt}') IS NOT NULL))"
           for _, fmt, pattern in tried]
    row = connection.execute(
        f"SELECT {', '.join(bad)} FROM (SELECT DISTINCT {column} AS v FROM r "
        f"WHERE {column} IS NOT NULL AND NOT ({_placeholder(name)}))").fetchone()
    fits = [label for (label, _, _), failures in zip(tried, row) if failures == 0]
    if not fits:
        return None, False
    return fits[0], _MONTH_FIRST_TWIN.get(fits[0]) in fits


def profile_csv(path: Path) -> FileProfile:
    params = {"path": str(path)}
    connection = duckdb.connect(":memory:")
    try:
        try:
            connection.execute(f"CREATE TABLE r AS SELECT * FROM {_READ_TEXT}", params)  # the raw text
            try:
                connection.execute(f"CREATE TABLE t AS SELECT * FROM {_READ}", params)
            except duckdb.Error:  # e.g. the sniffer fails on mixed day-first and month-first dates:
                connection.execute("CREATE TABLE t AS SELECT * FROM r")  # every column is text then
            described = connection.execute("DESCRIBE t").fetchall()
            parts = ["count(*)"]
            for name, type_ in ((row[0], row[1]) for row in described):
                column = _quoted(name)
                parts += [f"count({column})", f"count(DISTINCT {column})"]
                if type_ == "VARCHAR":
                    parts += [f"count(*) FILTER (WHERE {_placeholder(name)})",
                              f"count(DISTINCT {column}) FILTER (WHERE NOT ({_placeholder(name)}))"]
            counts = connection.execute(f"SELECT {', '.join(parts)} FROM t").fetchone()
        except duckdb.Error as exc:
            raise NotATable(" ".join(str(exc).splitlines()[:2])) from None  # DuckDB's own first two lines
        rows = counts[0]
        at = 1
        columns = []
        for name, type_ in ((row[0], row[1]) for row in described):
            non_null, distinct = counts[at], counts[at + 1]
            at += 2
            text = type_ == "VARCHAR"
            placeholders, real = (counts[at], counts[at + 1]) if text else (0, distinct)
            at += 2 if text else 0
            nulls = rows - non_null
            all_empty = rows > 0 and non_null - placeholders == 0
            samples = [] if all_empty else [r[0] for r in connection.execute(
                f"SELECT DISTINCT left({_quoted(name)}, {SAMPLE_WIDTH}) AS v FROM r "
                f"WHERE {_quoted(name)} IS NOT NULL AND NOT ({_placeholder(name)}) ORDER BY v LIMIT {SAMPLE_COUNT}"
            ).fetchall()]
            seen = [r[0] for r in connection.execute(
                f"SELECT DISTINCT {_quoted(name)} FROM t WHERE {_placeholder(name)} ORDER BY 1 LIMIT {SAMPLE_COUNT}"
            ).fetchall()] if placeholders else []
            fmt, ambiguous = _text_date_format(connection, name, samples, text) if real else (None, False)
            columns.append(FileColumn(
                name=name, type=type_, null_count=nulls, null_rate=round(nulls / rows, 6) if rows else None,
                distinct_count=distinct, placeholder_count=placeholders, placeholder_values=seen,
                text_date_format=fmt, date_format_ambiguous=ambiguous, all_empty=all_empty,
                constant=real == 1 and rows >= 2, samples=samples))
    finally:
        connection.close()
    return FileProfile(row_count=rows, columns=columns, engine=ENGINE, issues=issues_of(rows, columns))


def duplicate_key_found(path: Path, key_columns: list[str]) -> DuplicateKey | None:
    """The same count as duplicate_key_issue, as the ProfilerAgent's payload holds it: extra rows and up to five
    duplicated key values (several columns joined by ' | ')."""
    found = _duplicate_groups(path, key_columns)
    if not found:
        return None
    return DuplicateKey(columns=list(key_columns), extra_rows=sum(g[-1] - 1 for g in found),
                        examples=[" | ".join(g[:-1]) for g in found[:5]])


def duplicate_key_issue(path: Path, key_columns: list[str]) -> ProfileIssue | None:
    """Rows beyond the first of each key (a null in the key is a value like any other), or None when the key is
    unique. The detail names up to five duplicated key values."""
    groups = _duplicate_groups(path, key_columns)
    if not groups:
        return None
    extra = sum(g[-1] - 1 for g in groups)
    named = "; ".join(f"{' | '.join(g[:-1])} ({g[-1]} rows)" for g in groups[:5])
    more = f"; and {len(groups) - 5} more" if len(groups) > 5 else ""
    return ProfileIssue(kind="duplicate_key", columns=list(key_columns), count=extra,
                        detail=f"{extra} extra row(s) repeat a key. Duplicated: {named}{more}.")


def incomplete_records_found(path: Path, required_columns: list[str]) -> IncompleteRecords | None:
    """T1: how many rows have a null (an empty cell) in any of the required columns, by DuckDB over the file. None when
    no required column is known or the file lacks every one of them: there is nothing to say then."""
    if not required_columns:
        return None
    connection = duckdb.connect(":memory:")
    try:
        try:
            have = [r[0] for r in connection.execute(f"DESCRIBE SELECT * FROM {_READ}", {"path": str(path)}).fetchall()]
        except duckdb.Error as exc:
            raise NotATable(" ".join(str(exc).splitlines()[:2])) from None
        required = [c for c in required_columns if c in have]
        if not required:
            return None
        missing = " OR ".join(f"{_quoted(c)} IS NULL" for c in required)
        total, incomplete = connection.execute(
            f"SELECT count(*), count(*) FILTER (WHERE {missing}) FROM {_READ}", {"path": str(path)}).fetchone()
    finally:
        connection.close()
    return IncompleteRecords(incomplete=incomplete, total=total, required_columns=required)


def _duplicate_groups(path: Path, key_columns: list[str]) -> list[tuple]:
    if not key_columns:
        raise ValueError("a key needs at least one column")
    connection = duckdb.connect(":memory:")
    try:
        try:
            have = [row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM {_READ}", {"path": str(path)}).fetchall()]
        except duckdb.Error as exc:
            raise NotATable(" ".join(str(exc).splitlines()[:2])) from None
        unknown = [k for k in key_columns if k not in have]
        if unknown:
            raise ValueError(f"unknown key column {unknown[0]!r}")
        key = ", ".join(_quoted(k) for k in key_columns)
        shown = ", ".join(f"coalesce(CAST({_quoted(k)} AS VARCHAR), '(empty)')" for k in key_columns)
        groups = connection.execute(
            f"SELECT {shown}, n FROM (SELECT {key}, count(*) AS n FROM {_READ} GROUP BY {key} HAVING count(*) > 1) "
            f"ORDER BY {shown}", {"path": str(path)}).fetchall()
    finally:
        connection.close()
    return groups


# ---------------------------------------------------------------- the spec and coverage (assumptions 8 and 9)


def _phrase(text: str) -> str:
    """As S2 compares a ticked term with a source's other fields: lower case, '_' and spaces as one space."""
    return " ".join(text.lower().replace("_", " ").split())


def spec_of(entry: SourceEntry) -> SourceSpec:
    return SourceSpec(source_code=entry.code, elements=entry.elements, other_fields=entry.other_fields,
                      columns=entry.columns)


def coverage_of(requirement: Requirement, entry: SourceEntry, file: FileProfile | None) -> RequirementCoverage:
    """Each requested element and ticked term: declared by the source, its column, and that column's
    statistics in the file. Without a file (spec only), from the spec alone."""
    by_field = {_phrase(field): column for column, field in entry.columns.items()}
    stats = {} if file is None else {c.name: c for c in file.columns}
    wanted = [(k, "element", k) for k in requirement.elements] + [(t, "term", _phrase(t)) for t in requirement.terms]
    items, missing = [], []
    for item, kind, key in wanted:
        declared = (key in entry.elements) if kind == "element" else (key in {_phrase(f) for f in entry.other_fields})
        column = by_field.get(_phrase(key)) if declared else None
        found = stats.get(column) if column else None
        in_file = None if file is None else found is not None
        items.append(ItemCoverage(item=item, kind=kind, declared=declared, column=column, in_file=in_file,
                                  null_rate=found.null_rate if found else None,
                                  distinct_count=found.distinct_count if found else None))
        if not declared or in_file is False:
            missing.append(item)
    return RequirementCoverage(requirement_id=requirement.id, items=items, missing=missing)
