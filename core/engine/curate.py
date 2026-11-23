"""The curated stage: the mapped columns only, renamed to their element keys, transformed as approved, and cast to
the element's type (phase 6 plan, assumptions 14, 15 and 16; phase 7.8 task 4.2).

The SQL here is the engine's own (trusted), built from the current mappings, the approved transformations
(core/engine/transform.py: fixed templates only) and the glossary's data types; column names come from outside, so
each is used only as a quoted identifier with its quotes doubled. Rules never reach this module: they run later,
in the locked sandbox.

The uncast check, with transformations. A non-NULL raw value whose transformed-then-cast value is NULL normally
holds the file. The one exception is a value an approved placeholder_to_null step nulled on purpose. This is the
explicit "nulled by placeholder" predicate: the check evaluates the column's steps up to and including its last
placeholder_to_null (transform.until_last_placeholder()); if that expression is already NULL for the row, the NULL
is the placeholder's and the row is not reported. A value that parse_date or cast could not turn into a value is
not NULL at that point, so it is still reported (values_do_not_cast, as before).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.engine.transform import TYPE_SQL, chain, column_sql, quoted, until_last_placeholder

__all__ = ["Found", "TYPE_SQL", "curated_rows_sql", "curated_sql", "duplicate_probe_sql", "keep_last_sql",
           "key_columns_found", "match_header", "quoted", "uncast_sql"]


@dataclass(frozen=True)
class Found:
    """A mapped column found in the file: its mapped name, the file's own spelling, its element key and type, and
    the steps of its approved transformation (none for most columns)."""

    source_field: str
    header: str
    element_key: str
    data_type: str
    steps: tuple = ()


def match_header(columns, header: list[str], transformations=None) -> tuple[list[Found], list[str], list[str]]:
    """(found, missing, unmapped). A mapping's source field matches a file column in any case. `missing` are
    mapped fields the file lacks (mapped names); `unmapped` are file columns no current mapping names.
    `transformations`: {source field in lower case: steps}."""
    by_lower = {name.lower(): name for name in header}
    steps_of = transformations or {}
    found, missing = [], []
    for column in columns:
        actual = by_lower.get(column.source_field.lower())
        if actual is None:
            missing.append(column.source_field)
        else:
            found.append(Found(column.source_field, actual, column.element_key, column.data_type,
                               tuple(steps_of.get(column.source_field.lower(), ()))))
    used = {f.header for f in found}
    return found, missing, [name for name in header if name not in used]


def curated_sql(found: list[Found]) -> str:
    """Rows keep their file number `_row`; an empty cell is already NULL; a value that does not cast is NULL here
    and found by uncast_sql() (it is never left NULL silently)."""
    casts = ", ".join(f"TRY_CAST({column_sql(f.header, f.steps)} AS {TYPE_SQL[f.data_type]}) AS {quoted(f.element_key)}"
                      for f in found)
    return f"CREATE TABLE curated AS SELECT _row, {casts} FROM raw"


def uncast_sql(f: Found) -> str:
    """The rows whose value in this column is not empty and did not cast (and was not nulled by a placeholder)."""
    sql = (f"SELECT r._row, r.{quoted(f.header)} FROM raw r JOIN curated c ON c._row = r._row "
           f"WHERE r.{quoted(f.header)} IS NOT NULL AND c.{quoted(f.element_key)} IS NULL")
    placeholders = until_last_placeholder(f.steps)
    if placeholders:  # nulled on purpose by an approved placeholder: not an uncast value
        sql += f" AND ({chain('r.' + quoted(f.header), placeholders)}) IS NOT NULL"
    return sql + " ORDER BY r._row"


def duplicate_probe_sql(keys: list[Found]) -> str:
    """The rows whose key (the key columns' element keys) repeats, with the highest `_row` of each. Rows with an empty
    key column have no key. Run whenever the feed has key columns present in the file (step 13, duplicate policy)."""
    columns = ", ".join(quoted(k.element_key) for k in keys)
    present = " AND ".join(f"{quoted(k.element_key)} IS NOT NULL" for k in keys)
    return (f"SELECT _row, {columns}, max(_row) OVER (PARTITION BY {columns}) AS last_row FROM "
            f"(SELECT *, count(*) OVER (PARTITION BY {columns}) AS n FROM curated WHERE {present}) WHERE n > 1 "
            "ORDER BY _row")


def keep_last_sql(keys: list[Found]) -> str:
    """The `keep_last` policy: only the row with the highest `_row` of each repeated key stays in curated."""
    columns = ", ".join(quoted(k.element_key) for k in keys)
    present = " AND ".join(f"{quoted(k.element_key)} IS NOT NULL" for k in keys)
    return (f"DELETE FROM curated WHERE _row IN (SELECT _row FROM (SELECT _row, max(_row) OVER "
            f"(PARTITION BY {columns}) AS last_row FROM curated WHERE {present}) WHERE _row <> last_row)")


def key_columns_found(found: list[Found], key_columns: list[str]) -> tuple[list[Found], list[str]]:
    """(the key columns that are mapped columns of the file, the names ignored): a key column is named by its mapped
    name or the file's spelling, in any case."""
    by_name = {name.lower(): f for f in found for name in (f.source_field, f.header)}
    keys = [by_name[c.lower()] for c in key_columns if c.lower() in by_name]
    return keys, [c for c in key_columns if c.lower() not in by_name]


def curated_rows_sql(found: list[Found]) -> str:
    return f"SELECT _row, {', '.join(quoted(f.element_key) for f in found)} FROM curated ORDER BY _row"
