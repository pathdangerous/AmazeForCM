"""Direct file intake: any accepted tabular file becomes ONE canonical CSV plus a manifest.

The canonical CSV is UTF-8 without BOM, comma-delimited, minimally quoted (Python's csv module), with
`\\n` line ends, exactly one header row, then the data rows; an empty cell is an empty field. Steps 02-20
read CSV through DuckDB, so they keep working unchanged whatever the upload was. The manifest (JSON)
records how the file was read: encoding, delimiter, header row, skipped rows, cell types per column.
CSV is read here; XLSX and PDF readers fill the same Table and Manifest. No database, model or port.
"""

from core.intake.canonical import canonical_csv, manifest_json
from core.intake.csv_reader import read_csv_bytes
from core.intake.model import (
    CellType,
    IntakeColumn,
    Manifest,
    NeedsImageExtraction,
    NotATable,
    SkippedRow,
    Table,
    UnsupportedEncoding,
    clean_header,
)
from core.intake.pdf_reader import PdfInspection, inspect_pdf, read_pdf_bytes, render_pages_png
from core.intake.xlsx_reader import list_sheets, read_xlsx_bytes

__all__ = [
    "CellType",
    "IntakeColumn",
    "Manifest",
    "NeedsImageExtraction",
    "NotATable",
    "PdfInspection",
    "SkippedRow",
    "Table",
    "UnsupportedEncoding",
    "canonical_csv",
    "clean_header",
    "inspect_pdf",
    "list_sheets",
    "manifest_json",
    "read_csv_bytes",
    "read_pdf_bytes",
    "read_xlsx_bytes",
    "render_pages_png",
]
