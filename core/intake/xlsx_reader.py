"""Read one worksheet of an .xlsx workbook into the intake model.

Cached values only (`data_only=True`): a formula cell gives the value the spreadsheet program saved with it;
a workbook written by a library that does not calculate (openpyxl) has none, so such a cell reads as empty.
A merged range's value lives in its top-left cell only; the other cells of the range read as empty.
The reader never guesses a date inside text: a text cell stays text (profiling decides, in stage S3).
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from collections import Counter
from decimal import Decimal
from typing import Any

import openpyxl

from core.intake.model import CellType, IntakeColumn, Manifest, NotATable, SkippedRow, Table, clean_header

_NOT_READABLE = "not a readable .xlsx workbook"


def _open(data: bytes) -> openpyxl.Workbook:
    try:
        return openpyxl.load_workbook(io.BytesIO(data), read_only=False, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError, TypeError, AttributeError, EOFError) as exc:
        raise NotATable(_NOT_READABLE) from exc
    except Exception as exc:  # openpyxl raises its own errors for broken packages
        raise NotATable(_NOT_READABLE) from exc


def list_sheets(data: bytes) -> list[str]:
    """Sheet names in workbook order (hidden sheets included)."""
    wb = _open(data)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _cell(value: Any) -> tuple[str | None, CellType]:
    """A cell value as canonical text, and its source type."""
    if value is None:
        return None, "empty"
    if isinstance(value, bool):
        return ("true" if value else "false"), "bool"
    if isinstance(value, int):
        return str(value), "number"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value)), "number"
        return repr(value), "number"
    if isinstance(value, Decimal):
        return str(value), "number"
    if isinstance(value, dt.datetime):
        if value.time() == dt.time(0):
            return value.date().isoformat(), "date"
        return value.isoformat(), "datetime"
    if isinstance(value, dt.date):
        return value.isoformat(), "date"
    if isinstance(value, dt.time):
        return value.isoformat(), "text"
    text = value if isinstance(value, str) else str(value)
    if text == "":
        return None, "empty"
    return text, "text"


def read_xlsx_bytes(
    data: bytes, *, original_name: str, sheet: str | None = None, header_row: int | None = None
) -> tuple[Table, Manifest]:
    wb = _open(data)
    try:
        names = list(wb.sheetnames)
        if not names:
            raise NotATable("the workbook has no sheets")
        if sheet is None:
            sheet = names[0]
        elif sheet not in names:
            raise NotATable(f"no sheet named {sheet!r} in the workbook")
        ws = wb[sheet]
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        merged_rows: set[int] = set()
        for rng in ws.merged_cells.ranges:
            if rng.max_col > rng.min_col:
                merged_rows.update(range(rng.min_row, rng.max_row + 1))
    finally:
        wb.close()

    cells = [[_cell(v) for v in row] for row in grid]
    width = 0
    for row in cells:
        for i in range(len(row) - 1, width - 1, -1):
            if row[i][0] is not None:
                width = i + 1
                break
    if width == 0:
        raise NotATable("no header row: no cells")
    texts: list[list[str | None]] = [[c[0] for c in row[:width]] + [None] * (width - len(row)) for row in cells]
    types: list[list[CellType]] = [[c[1] for c in row[:width]] + ["empty"] * (width - len(row)) for row in cells]

    def skip_reason(n: int) -> str | None:  # n is the 1-based sheet row
        if n in merged_rows:
            return "merged"
        if all(t is None for t in texts[n - 1]):
            return "empty"
        return None

    if header_row is None:
        live = [n for n in range(1, len(texts) + 1) if skip_reason(n) is None]
        if not live:
            raise NotATable("no header row: no cells")
        count = {n: sum(1 for t in texts[n - 1] if t is not None) for n in live}
        widest = max(count.values())
        found = None
        for n in live:
            filled = count[n]
            if filled == widest and sum(1 for t in types[n - 1] if t == "text") * 2 > filled:
                found = n
                break
        if found is None:
            raise NotATable("no header row found")
        head = found
    else:
        head = header_row
        if head < 1 or head > len(texts):
            raise NotATable(f"header row {header_row} is not in the sheet")
    raw_header = texts[head - 1]
    if all(t is None for t in raw_header):
        raise NotATable(f"header row {head} has no columns")
    header = clean_header([t or "" for t in raw_header])

    skipped: list[SkippedRow] = []
    body: list[list[str | None]] = []
    counts: list[Counter[CellType]] = [Counter() for _ in range(width)]
    for n in range(1, len(texts) + 1):
        if n == head:
            continue
        reason = skip_reason(n)
        if n < head:
            skipped.append(SkippedRow(row=n, reason=reason or "before_header"))  # type: ignore[arg-type]
            continue
        if reason is not None:
            skipped.append(SkippedRow(row=n, reason=reason))  # type: ignore[arg-type]
            continue
        body.append(texts[n - 1])
        for pos, t in enumerate(types[n - 1]):
            counts[pos][t] += 1

    columns = [IntakeColumn(name=name, position=pos + 1, cell_types=dict(counts[pos])) for pos, name in enumerate(header)]
    table = Table(header=header, rows=body, columns=columns, header_row=head, skipped=skipped)
    manifest = Manifest(
        format="xlsx",
        original_name=original_name,
        encoding=None,
        delimiter=None,
        sheet=sheet,
        sheets=names,
        header_row=head,
        row_count=len(body),
        columns=columns,
        skipped=skipped,
        reader=f"openpyxl {openpyxl.__version__}",
    )
    return table, manifest
