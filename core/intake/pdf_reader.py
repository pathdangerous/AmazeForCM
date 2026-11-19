"""Read tables from PDFs (decision D4: tables only; free text and forms are out).

Text PDFs go through pdfplumber: ruled tables first, then a text-alignment fallback. A PDF whose pages are
all images raises NeedsImageExtraction (task 2.3 sends those pages to the model, the operator confirms).
`render_pages_png` renders pages for that path. No database, model or port.
"""

from __future__ import annotations

import io
from collections import Counter
from typing import Any, Literal

import pdfplumber
import pypdfium2
from pydantic import BaseModel, ConfigDict

from core.intake.model import (
    CellType,
    IntakeColumn,
    Manifest,
    NeedsImageExtraction,
    NotATable,
    SkippedRow,
    Table,
    clean_header,
)

_NOT_READABLE = "not a readable PDF"
MIN_TEXT_CHARS = 20


class PdfInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pages: int
    text_pages: list[int]  # 1-based pages with at least MIN_TEXT_CHARS extracted characters
    image_pages: list[int]  # pages with (almost) no text and at least one image
    kind: Literal["text", "image", "mixed", "empty"]


def _open(data: bytes) -> pdfplumber.PDF:
    try:
        return pdfplumber.open(io.BytesIO(data))
    except Exception as exc:  # pdfminer raises its own errors for junk, truncated and encrypted files
        raise NotATable(_NOT_READABLE) from exc


def _classify(pdf: pdfplumber.PDF) -> PdfInspection:
    text_pages: list[int] = []
    image_pages: list[int] = []
    for n, page in enumerate(pdf.pages, start=1):
        if len(page.chars) >= MIN_TEXT_CHARS:
            text_pages.append(n)
        elif page.images:
            image_pages.append(n)
    if text_pages and image_pages:
        kind: Literal["text", "image", "mixed", "empty"] = "mixed"
    elif text_pages:
        kind = "text"
    elif image_pages:
        kind = "image"
    else:
        kind = "empty"
    return PdfInspection(pages=len(pdf.pages), text_pages=text_pages, image_pages=image_pages, kind=kind)


def inspect_pdf(data: bytes) -> PdfInspection:
    pdf = _open(data)
    try:
        return _classify(pdf)
    except NotATable:
        raise
    except Exception as exc:
        raise NotATable(_NOT_READABLE) from exc
    finally:
        pdf.close()


def _clean(cell: Any) -> str | None:
    if cell is None:
        return None
    text = " ".join(str(cell).split())
    return text or None


def _page_table(page: Any) -> list[list[str | None]] | None:
    """The largest table on a page (lines strategy, then text alignment), cells cleaned."""
    found = page.extract_tables()
    if not found:
        found = [
            t
            for t in page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "text"})
            if len(t) >= 2 and max((len(r) for r in t), default=0) >= 2
        ]
    if not found:
        return None
    best = max(found, key=lambda t: len(t) * max((len(r) for r in t), default=0))
    return [[_clean(c) for c in row] for row in best]


def read_pdf_bytes(data: bytes, *, original_name: str, header_row: int | None = None) -> tuple[Table, Manifest]:
    pdf = _open(data)
    try:
        info = _classify(pdf)
        if info.kind == "image":
            raise NeedsImageExtraction(info.image_pages)
        tables: dict[int, list[list[str | None]]] = {}
        for n in info.text_pages:
            t = _page_table(pdf.pages[n - 1])
            if t:
                tables[n] = t
    except (NotATable, NeedsImageExtraction):
        raise
    except Exception as exc:
        raise NotATable(_NOT_READABLE) from exc
    finally:
        pdf.close()

    if not tables:
        raise NotATable("no table found in the PDF; free text and forms are not read")

    first_page = min(tables)
    first = tables[first_page]
    head = 1 if header_row is None else header_row
    if head < 1 or head > len(first):
        raise NotATable(f"header row {header_row} is not in the first table")
    width = len(first[head - 1])
    raw_header = first[head - 1]
    if all(c is None for c in raw_header):
        raise NotATable(f"header row {head} has no columns")
    header = clean_header([c or "" for c in raw_header])
    norm_header = [c or "" for c in raw_header]

    skipped: list[SkippedRow] = []
    body: list[list[str | None]] = []
    counts: list[Counter[CellType]] = [Counter() for _ in range(width)]
    used: list[int] = []
    ignored = [n for n in range(1, info.pages + 1) if n not in tables]
    running = 0  # 1-based row number across the joined tables
    for n in sorted(tables):
        t = tables[n]
        if max(len(r) for r in t) != width or min(len(r) for r in t) != width:
            ignored.append(n)
            continue
        used.append(n)
        for idx, row in enumerate(t, start=1):
            running += 1
            if n == first_page and idx == head:
                continue
            if n == first_page and idx < head:
                skipped.append(SkippedRow(row=running, reason="before_header"))
                continue
            if all(c is None for c in row):
                skipped.append(SkippedRow(row=running, reason="empty"))
                continue
            if n != first_page and idx == 1 and [c or "" for c in row] == norm_header:
                skipped.append(SkippedRow(row=running, reason="repeated_header"))
                continue
            body.append(row)
            for pos, cell in enumerate(row):
                counts[pos]["empty" if cell is None else "text"] += 1

    columns = [IntakeColumn(name=name, position=pos + 1, cell_types=dict(counts[pos])) for pos, name in enumerate(header)]
    table = Table(header=header, rows=body, columns=columns, header_row=head, skipped=skipped)
    manifest = Manifest(
        format="pdf",
        original_name=original_name,
        encoding=None,
        delimiter=None,
        sheet=None,
        sheets=[],
        header_row=head,
        row_count=len(body),
        columns=columns,
        skipped=skipped,
        pages=used,
        ignored_pages=sorted(ignored),
        extraction="text_pdf",
        reader=f"pdfplumber {pdfplumber.__version__}",
    )
    return table, manifest


def render_pages_png(data: bytes, pages: list[int], *, scale: float = 2.0, max_pages: int = 10) -> list[bytes]:
    """Render 1-based pages to PNG bytes, in the order given (for the model's image input)."""
    if len(pages) > max_pages:
        raise NotATable(f"an image PDF of more than {max_pages} pages is not read")
    try:
        doc = pypdfium2.PdfDocument(data)
    except Exception as exc:
        raise NotATable(_NOT_READABLE) from exc
    try:
        total = len(doc)
        out: list[bytes] = []
        for n in pages:
            if n < 1 or n > total:
                raise NotATable(f"page {n} is not in the PDF")
            buf = io.BytesIO()
            doc[n - 1].render(scale=scale).to_pil().save(buf, "PNG")
            out.append(buf.getvalue())
        return out
    except NotATable:
        raise
    except Exception as exc:
        raise NotATable(_NOT_READABLE) from exc
    finally:
        doc.close()
