"""Approved transformations as fixed SQL templates (phase 7.8 plan, assumption 28; task 4.2).

A transformation is an ordered list of steps from a fixed safe set (core/domain/transformation.py). Nothing a model
or a person typed reaches SQL except through the templates here: a value (a placeholder) becomes a string literal
with its quotes doubled, a date format is looked up in a fixed map, a type in TYPE_SQL, and a column is only ever a
quoted identifier. This is the engine's own trusted SQL (it runs before the sandbox is locked); rule SQL still
runs only in the sandbox.

Templates, over an already quoted column expression x:
    trim                 trim(x)
    placeholder_to_null  CASE WHEN upper(trim(x)) IN (<values, upper-cased>) THEN NULL ELSE x END
    parse_date           CAST(try_strptime(x, '<pattern>') AS DATE)   a value that does not parse is NULL
    cast                 TRY_CAST(x AS <type>)
    rename               no SQL: it sets the column's label (the engine's `labels`)

A value a template turns into NULL by failing (parse_date, cast) is found by the uncast check and holds the file;
only a placeholder an approved transformation nulls on purpose is exempt (see until_last_placeholder()).
"""

from __future__ import annotations

from collections.abc import Sequence

from core.domain.transformation import Cast, ParseDate, PlaceholderToNull, Rename, Trim, TransformStep

TYPE_SQL = {"string": "VARCHAR", "integer": "BIGINT", "decimal": "DECIMAL(38,10)", "date": "DATE",
            "datetime": "TIMESTAMP", "boolean": "BOOLEAN"}

STRPTIME = {"DD-MM-YYYY": "%d-%m-%Y", "MM-DD-YYYY": "%m-%d-%Y", "DD/MM/YYYY": "%d/%m/%Y", "MM/DD/YYYY": "%m/%d/%Y",
            "YYYY-MM-DD": "%Y-%m-%d", "YYYYMMDD": "%Y%m%d", "DD.MM.YYYY": "%d.%m.%Y"}


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def literal(text: str) -> str:
    """A SQL string literal: quotes doubled. (DuckDB has no backslash escapes in a plain literal.)"""
    return "'" + text.replace("'", "''") + "'"


def step_sql(expr: str, step: TransformStep) -> str:
    """The SQL of one step over the expression `expr`. `rename` changes nothing in the value."""
    if isinstance(step, Trim):
        return f"trim({expr})"
    if isinstance(step, PlaceholderToNull):
        listed = ", ".join(literal(v.upper()) for v in step.values)
        return f"CASE WHEN upper(trim({expr})) IN ({listed}) THEN NULL ELSE {expr} END"
    if isinstance(step, ParseDate):
        return f"CAST(try_strptime({expr}, {literal(STRPTIME[step.format])}) AS DATE)"
    if isinstance(step, Cast):
        return f"TRY_CAST({expr} AS {TYPE_SQL[step.type]})"
    if isinstance(step, Rename):
        return expr
    raise TypeError(f"not a transformation step: {step!r}")


def chain(expr: str, steps: Sequence[TransformStep]) -> str:
    for step in steps:
        expr = step_sql(expr, step)
    return expr


def column_sql(header: str, steps: Sequence[TransformStep]) -> str:
    """The steps in order over the file's own column."""
    return chain(quoted(header), steps)


def until_last_placeholder(steps: Sequence[TransformStep]) -> list[TransformStep]:
    """The steps up to and including the last placeholder_to_null, or [] if there is none. A value of the raw
    column that is NOT NULL and is NULL after exactly these steps was nulled on purpose by an approved placeholder;
    a value that is NULL only after parse_date or cast failed to parse or cast, and holds the file."""
    last = max((i for i, s in enumerate(steps) if isinstance(s, PlaceholderToNull)), default=-1)
    return list(steps[: last + 1])


def label_of(steps: Sequence[TransformStep]) -> str | None:
    """The `to` of a rename step, if the column has one."""
    return next((s.to for s in steps if isinstance(s, Rename)), None)
