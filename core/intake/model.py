"""The intake model: what a reader found in an uploaded file (CSV now; XLSX and PDF readers use it too)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

CellType = Literal["text", "number", "date", "datetime", "bool", "empty"]
SkipReason = Literal["empty", "merged", "before_header", "repeated_header", "short_row"]


class NotATable(ValueError):
    """The file has no header row, no columns, or is binary data. An expected case, not a crash."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class UnsupportedEncoding(ValueError):
    """The bytes are not text in an encoding the reader knows."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NeedsImageExtraction(Exception):
    """The PDF has only image pages: an expected case. The table must come from the model's image input (task 2.3)."""

    def __init__(self, pages: list[int]):
        super().__init__(f"image pages {pages} need image extraction")
        self.pages = pages


class SkippedRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    row: int  # 1-based in the source sheet or file (for CSV: the record number, quoted newlines count once)
    reason: SkipReason


class IntakeColumn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    position: int  # 1-based
    cell_types: dict[CellType, int]  # source cell types seen in this column's data rows


class Table(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    header: list[str]
    rows: list[list[str | None]]
    columns: list[IntakeColumn]
    header_row: int  # 1-based
    skipped: list[SkippedRow]


class Manifest(BaseModel):
    """How the file was read. Beside the canonical CSV, never inside it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal["csv", "xlsx", "pdf"]
    original_name: str
    encoding: str | None
    delimiter: str | None
    sheet: str | None
    sheets: list[str]
    header_row: int
    row_count: int
    columns: list[IntakeColumn]
    skipped: list[SkippedRow]
    pages: list[int] = []
    ignored_pages: list[int] = []  # PDF pages not joined into the table (image pages, prose, another width)
    extraction: Literal["parsed", "text_pdf", "image_pdf"] = "parsed"
    reader: str  # e.g. "csv stdlib", later "openpyxl 3.1.5"


def clean_header(cells: list[str]) -> list[str]:
    """Strip names; an empty one becomes column_<n> (1-based); duplicates get _2, _3 suffixes."""
    names = [(c or "").strip() or f"column_{i}" for i, c in enumerate(cells, start=1)]
    seen: dict[str, int] = {}
    taken = set(names)
    out: list[str] = []
    for name in names:
        count = seen.get(name, 0) + 1
        seen[name] = count
        if count == 1:
            out.append(name)
            continue
        candidate = f"{name}_{count}"
        while candidate in taken:
            count += 1
            candidate = f"{name}_{count}"
        seen[name] = count
        taken.add(candidate)
        out.append(candidate)
    return out
