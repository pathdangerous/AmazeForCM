"""Read a delimited text file into the intake model: encoding, delimiter, header row, data rows."""

from __future__ import annotations

import csv
import io
from collections import Counter

from core.intake.model import (
    CellType,
    IntakeColumn,
    Manifest,
    NotATable,
    SkippedRow,
    Table,
    UnsupportedEncoding,
    clean_header,
)

DELIMITERS = (",", ";", "\t", "|")
_SAMPLE_LINES = 200


def _decode(data: bytes) -> tuple[str, str]:
    if not data:
        raise NotATable("empty file")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="strict"), "utf-8-sig"
    if data.startswith(b"\xff\xfe"):
        return _utf16(data[2:], "utf-16-le"), "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return _utf16(data[2:], "utf-16-be"), "utf-16-be"
    if b"\x00" in data:
        raise NotATable("binary")
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError as exc:
        raise UnsupportedEncoding("not UTF-8 and not cp1252") from exc


def _utf16(data: bytes, codec: str) -> str:
    try:
        return data.decode(codec)
    except UnicodeDecodeError as exc:
        raise UnsupportedEncoding(f"invalid {codec}") from exc


def _parse(text: str, delimiter: str) -> list[list[str]]:
    try:
        return [[c[:-1] if c.endswith("\r") else c for c in row] for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]
    except csv.Error as exc:
        raise NotATable(f"not readable as delimited text: {exc}") from exc


def _choose_delimiter(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()][:_SAMPLE_LINES]
    sample = "\n".join(lines)
    best, best_score = ",", (0, 0)
    for delimiter in DELIMITERS:
        try:
            rows = _parse(sample, delimiter)
        except NotATable:
            continue
        counts = Counter(len(r) for r in rows if len(r) > 1)
        if not counts:
            continue
        score = max((n, width) for width, n in counts.items())
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _is_number(value: str) -> bool:
    try:
        float(value.strip())
    except ValueError:
        return False
    return True


def _looks_like_header(row: list[str]) -> bool:
    filled = [c for c in row if c.strip()]
    return bool(filled) and sum(1 for c in filled if not _is_number(c)) * 2 > len(filled)


def _detect_header(rows: list[list[str]]) -> int:
    """Index (0-based) of the header row."""
    widths = Counter(len(r) for r in rows if any(c.strip() for c in r))
    if not widths:
        raise NotATable("no header row: no cells")
    modal = max(widths.items(), key=lambda kv: (kv[1], kv[0]))[0]
    for i, row in enumerate(rows):  # the strict rule: as many non-empty cells as the modal width
        if sum(1 for c in row if c.strip()) == modal and _looks_like_header(row):
            return i
    for i, row in enumerate(rows):  # a header with an empty name still has the modal width
        if len(row) == modal and _looks_like_header(row):
            return i
    raise NotATable("no header row found")


def read_csv_bytes(data: bytes, *, original_name: str, header_row: int | None = None) -> tuple[Table, Manifest]:
    text, encoding = _decode(data)
    delimiter = _choose_delimiter(text)
    rows = _parse(text, delimiter)
    if header_row is None:
        head = _detect_header(rows)
    else:
        head = header_row - 1
        if head < 0 or head >= len(rows):
            raise NotATable(f"header row {header_row} is not in the file")
    raw_header = rows[head]
    if not any(c.strip() for c in raw_header):
        raise NotATable(f"header row {head + 1} has no columns")
    header = clean_header(raw_header)
    width = len(header)

    skipped = [SkippedRow(row=i + 1, reason="before_header") for i in range(head)]
    body: list[list[str | None]] = []
    for i in range(head + 1, len(rows)):
        row = rows[i]
        if not any(row):
            skipped.append(SkippedRow(row=i + 1, reason="empty"))
            continue
        if row == raw_header:
            skipped.append(SkippedRow(row=i + 1, reason="repeated_header"))
            continue
        if len(row) > width:
            if any(row[width:]):
                raise NotATable(f"row {i + 1} has {len(row)} cells; the header has {width}")
            row = row[:width]
        body.append([c if c != "" else None for c in row] + [None] * (width - len(row)))

    columns = []
    for pos, name in enumerate(header):
        counts: Counter[CellType] = Counter("empty" if r[pos] is None else "text" for r in body)
        columns.append(IntakeColumn(name=name, position=pos + 1, cell_types=dict(counts)))

    table = Table(header=header, rows=body, columns=columns, header_row=head + 1, skipped=skipped)
    manifest = Manifest(
        format="csv",
        original_name=original_name,
        encoding=encoding,
        delimiter=delimiter,
        sheet=None,
        sheets=[],
        header_row=head + 1,
        row_count=len(body),
        columns=columns,
        skipped=skipped,
        reader="csv stdlib",
    )
    return table, manifest
