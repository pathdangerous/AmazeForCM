"""The curated stage: the mapped columns only, renamed to their element keys and cast to the element's type
(phase 6 plan, assumptions 14, 15 and 16).

The SQL here is the engine's own (trusted), built from the current mappings and the glossary's data types; column
names come from outside, so each is used only as a quoted identifier with its quotes doubled. Rules never reach
this module: they run later, in the locked sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass

TYPE_SQL = {"string": "VARCHAR", "integer": "BIGINT", "decimal": "DECIMAL(38,10)", "date": "DATE",
            "datetime": "TIMESTAMP", "boolean": "BOOLEAN"}


@dataclass(frozen=True)
class Found:
    """A mapped column found in the file: its mapped name, the file's own spelling, its element key and type."""

    source_field: str
    header: str
    element_key: str
    data_type: str


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def match_header(columns, header: list[str]) -> tuple[list[Found], list[str], list[str]]:
    """(found, missing, unmapped). A mapping's source field matches a file column in any case. `missing` are
    mapped fields the file lacks (mapped names); `unmapped` are file columns no current mapping names."""
    by_lower = {name.lower(): name for name in header}
    found, missing = [], []
    for column in columns:
        actual = by_lower.get(column.source_field.lower())
        if actual is None:
            missing.append(column.source_field)
        else:
            found.append(Found(column.source_field, actual, column.element_key, column.data_type))
    used = {f.header for f in found}
    return found, missing, [name for name in header if name not in used]


def curated_sql(found: list[Found]) -> str:
    """Rows keep their file number `_row`; an empty cell is already NULL; a value that does not cast is NULL here
    and found by uncast_sql() (it is never left NULL silently)."""
    casts = ", ".join(f"TRY_CAST({quoted(f.header)} AS {TYPE_SQL[f.data_type]}) AS {quoted(f.element_key)}"
                      for f in found)
    return f"CREATE TABLE curated AS SELECT _row, {casts} FROM raw"


def uncast_sql(f: Found) -> str:
    """The rows whose value in this column is not empty and did not cast."""
    return (f"SELECT r._row, r.{quoted(f.header)} FROM raw r JOIN curated c ON c._row = r._row "
            f"WHERE r.{quoted(f.header)} IS NOT NULL AND c.{quoted(f.element_key)} IS NULL ORDER BY r._row")


def curated_rows_sql(found: list[Found]) -> str:
    return f"SELECT _row, {', '.join(quoted(f.element_key) for f in found)} FROM curated ORDER BY _row"
