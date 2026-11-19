"""Step 00, direct file intake (phase 7.8, task 2.4; plan assumptions 16 to 20).

One operator onboards any file. There is no consumer request and no requirement: the operator uploads a file,
Amaze stores it and inspects it, the operator confirms how to read it, and a feed is created straight from it as a
KNOWN feed (a candidate feed whose file is already fetched), so step 02 profiles it exactly as it profiles a
fetched one. The reading is deterministic code (core/intake), with one exception (task 2.3, decision D4): the pages
of an image-only PDF go to the TableExtractorAgent, whose table the operator confirms.

Outcomes: inspected; too_large; unsupported_format; not_a_table; needs_image_extraction (an image-only PDF);
table_extracted (the model's reading of it, an extracted_table proposal); uploaded.

- extract_table() renders the image pages, calls the agent (no connection held) and writes the proposal; the agent
  is an argument, so this module never imports agents. confirm_upload(extracted_proposal_id=...) then approves
  that proposal as the operator and writes the canonical CSV from its header and rows.

- inspect_upload() has no database: over the size limit nothing is stored (too_large); an unsupported extension
  stores nothing; otherwise the original is stored at uploads/<upload id>/<safe name> and read.
- reinspect_upload() reads the stored original again with other options (another sheet or header row).
- confirm_upload() reads the stored original and parses it BEFORE it touches the session, so no database
  connection is held while the file is read (the pattern of steps 02 and S4). Then, in the caller's one
  transaction: the advisory lock on the new source code, the feed (connector dropzone, source_code
  upl_<feed code>), the canonical CSV and the manifest in the BlobStore as the feed's fetched file
  (raw/<feed code>/<date>/<fetch id>/<name>.csv, as step S4 keys it), the source_fetches row, the upload source and
  the "File ready to profile" notice to the backend team. The step flushes and never commits.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.domain.extraction import ExtractedTable, TableExtractionInput
from core.domain.proposal import Proposal
from core.domain.sourcing import AgentRun
from core.intake import (
    IntakeColumn,
    Manifest,
    NeedsImageExtraction,
    NotATable,
    Table,
    UnsupportedEncoding,
    canonical_csv,
    clean_header,
    manifest_json,
    read_csv_bytes,
    read_pdf_bytes,
    read_xlsx_bytes,
    render_pages_png,
)
from core.repositories.errors import ProposalAlreadyDecided, UploadNotFound
from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.repositories.upload_sources import UploadSourceRepository
from core.steps.errors import StepInputError
from core.steps.known_source import lock_source
from core.steps.result import StepResult
from core.steps.s4_fetch import blob_key
from ports.connectors.catalogue import SourceConditions
from ports.connectors.dropzone import write_to_dropzone
from ports.identity.base import User
from ports.notify.base import Notifier
from ports.storage.base import BlobStore

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_FEED_CODE = re.compile(r"^[a-z][a-z0-9_]*$")
FORMATS = {".csv": "csv", ".txt": "csv", ".tsv": "csv", ".xlsx": "xlsx", ".pdf": "pdf"}
TAKEN_FORMATS = ".csv, .txt, .tsv (delimited text), .xlsx and .pdf"
PREVIEW_ROWS = 20
MAX_IMAGE_PAGES = 10  # an image PDF of more pages is not sent to the model
DEFAULT_CONDITIONS = SourceConditions(licence="Internal use only", permitted_use="Internal analysis",
                                      redistribution="None")


class InvalidUploadInput(StepInputError):
    """The confirmation names a feed code that is not snake_case, a blank feed name, or a file name of a format
    that is not taken. The API answers 422."""


class Inspected(StepResult):
    outcome: Literal["inspected"] = "inspected"
    upload_id: UUID
    filename: str
    format: Literal["csv", "xlsx", "pdf"]
    sheets: list[str]  # the workbook's sheets; [] for a CSV or a PDF
    sheet: str | None
    header_row: int  # 1-based, as detected or as the operator asked
    encoding: str | None
    delimiter: str | None
    columns: list[str]
    row_count: int
    preview: list[list[str | None]]  # the first 20 data rows
    skipped: dict[str, int]  # rows left out, counted by reason


class TooLarge(StepResult):
    outcome: Literal["too_large"] = "too_large"
    size_bytes: int
    max_bytes: int
    detail: str


class UnsupportedFormat(StepResult):
    outcome: Literal["unsupported_format"] = "unsupported_format"
    filename: str
    detail: str


class NotATableResult(StepResult):
    outcome: Literal["not_a_table"] = "not_a_table"
    upload_id: UUID | None
    detail: str


class NeedsImageExtractionResult(StepResult):
    outcome: Literal["needs_image_extraction"] = "needs_image_extraction"
    upload_id: UUID
    pages: list[int]


class Uploaded(StepResult):
    outcome: Literal["uploaded"] = "uploaded"
    feed_id: UUID
    feed_code: str
    fetch_id: UUID
    row_count: int
    columns: list[str]


InspectResult = Annotated[
    Inspected | TooLarge | UnsupportedFormat | NotATableResult | NeedsImageExtractionResult,
    Field(discriminator="outcome")]
ConfirmResult = Annotated[Uploaded | NotATableResult, Field(discriminator="outcome")]


class TableExtracted(StepResult):
    """The model's reading of an image-only PDF, proposed to the operator (nothing is created yet)."""

    outcome: Literal["table_extracted"] = "table_extracted"
    proposal_id: UUID
    header: list[str]
    rows: list[list[str | None]]  # the first 20
    row_count: int
    confidence: float
    notes: list[str]


ExtractResult = Annotated[TableExtracted | NotATableResult, Field(discriminator="outcome")]


def safe_name(filename: str) -> str:
    """The file's own name (no folder, either separator) made safe for one blob-key segment."""
    base = re.split(r"[\\/]", filename)[-1]
    return _UNSAFE.sub("_", base).lstrip(".") or "upload"


def original_key(upload_id: UUID, filename: str) -> str:
    return f"uploads/{upload_id}/{safe_name(filename)}"


def format_of(filename: str) -> str | None:
    return FORMATS.get(os.path.splitext(filename.lower())[1])


def _unsupported(filename: str) -> UnsupportedFormat:
    return UnsupportedFormat(filename=filename, detail=f"{filename!r} is not a format Amaze takes; upload "
                                                       f"{TAKEN_FORMATS}")


def _read(data: bytes, fmt: str, *, filename: str, sheet: str | None, header_row: int | None) -> tuple[Table, Manifest]:
    """The reader of the format. Raises NotATable, UnsupportedEncoding or NeedsImageExtraction."""
    if fmt == "csv":
        return read_csv_bytes(data, original_name=filename, header_row=header_row)
    if fmt == "xlsx":
        return read_xlsx_bytes(data, original_name=filename, sheet=sheet, header_row=header_row)
    return read_pdf_bytes(data, original_name=filename, header_row=header_row)


def _inspect_stored(upload_id: UUID, data: bytes, fmt: str, *, filename: str, sheet: str | None,
                    header_row: int | None) -> Inspected | NotATableResult | NeedsImageExtractionResult:
    try:
        table, manifest = _read(data, fmt, filename=filename, sheet=sheet, header_row=header_row)
    except NeedsImageExtraction as exc:
        return NeedsImageExtractionResult(upload_id=upload_id, pages=list(exc.pages))
    except (NotATable, UnsupportedEncoding) as exc:
        return NotATableResult(upload_id=upload_id, detail=exc.detail)
    return Inspected(
        upload_id=upload_id, filename=filename, format=manifest.format, sheets=list(manifest.sheets),
        sheet=manifest.sheet, header_row=manifest.header_row, encoding=manifest.encoding,
        delimiter=manifest.delimiter, columns=list(table.header), row_count=manifest.row_count,
        preview=table.rows[:PREVIEW_ROWS], skipped=dict(Counter(s.reason for s in manifest.skipped)))


def inspect_upload(data: bytes, *, filename: str, blob_store: BlobStore, max_bytes: int, sheet: str | None = None,
                   header_row: int | None = None) -> InspectResult:
    """Store the original and read it. No database. Over max_bytes or of an unsupported format: nothing is stored."""
    if len(data) > max_bytes:
        return TooLarge(size_bytes=len(data), max_bytes=max_bytes,
                        detail=f"The file is {len(data)} bytes; the limit is {max_bytes} bytes")
    fmt = format_of(filename)
    if fmt is None:
        return _unsupported(filename)
    upload_id = uuid.uuid4()
    blob_store.put(original_key(upload_id, filename), data)
    return _inspect_stored(upload_id, data, fmt, filename=filename, sheet=sheet, header_row=header_row)


def _stored_key(upload_id: UUID, blob_store: BlobStore) -> str:
    keys = blob_store.list_keys(f"uploads/{upload_id}/")
    if not keys:
        raise UploadNotFound(f"No uploaded file {upload_id}")
    return keys[0]


def reinspect_upload(upload_id: UUID, *, filename: str, blob_store: BlobStore, sheet: str | None,
                     header_row: int | None) -> InspectResult:
    """The stored original read again with other options. UploadNotFound if no file is stored under the id."""
    data = blob_store.get(_stored_key(upload_id, blob_store))
    fmt = format_of(filename)
    if fmt is None:
        return _unsupported(filename)
    return _inspect_stored(upload_id, data, fmt, filename=filename, sheet=sheet, header_row=header_row)


def to_canonical(data: bytes, filename: str, options: dict | None) -> tuple[bytes, Manifest] | NotATableResult:
    """A recurring file as the canonical CSV: the reader of the file's extension with the feed's confirmed
    intake_options (sheet, header_row), the same reading confirm_upload() made of the first file (task 4.4).
    No database. NotATableResult (upload_id None) with the reason when the file is of a format Amaze does not take,
    has no table, or is a scanned PDF (its pages need the model and a person, which a recurring file does not get)."""
    options = options or {}
    fmt = format_of(filename)
    if fmt is None:
        return NotATableResult(upload_id=None, detail=_unsupported(filename).detail)
    try:
        table, manifest = _read(data, fmt, filename=filename, sheet=options.get("sheet"),
                                header_row=options.get("header_row"))
    except NeedsImageExtraction as exc:
        return NotATableResult(upload_id=None, detail=f"Pages {exc.pages} of {filename!r} are images; a recurring "
                                                      "file is not read through the model")
    except (NotATable, UnsupportedEncoding) as exc:
        return NotATableResult(upload_id=None, detail=exc.detail)
    return canonical_csv(table), manifest


class RunsExtractor(Protocol):
    def run(self, session: Session, agent_input: BaseModel, *, before_write=None) -> AgentRun: ...


def extract_table(session: Session, *, upload_id: UUID, filename: str, agent: RunsExtractor,
                  blob_store: BlobStore) -> TableExtracted | NotATableResult:
    """Read the image pages of a stored scanned PDF through the TableExtractorAgent (task 2.3, decision D4).

    The pattern of every agent-backed step with no database read of its own: the stored file is read and its pages
    rendered (at most 10) with no connection held, the model is called with the session in no transaction
    (Agent.run() refuses one), and then the extracted_table proposal is written in the caller's transaction. Nothing
    else is created: the feed comes when the operator confirms the table (confirm_upload).

    UploadNotFound (404). NotATableResult: the file is not a PDF, is read without the model, or has more than 10
    image pages; no model call is made. LLMUnavailable and LLMRefused are not caught here (503 and 422 in main.py)."""
    data = blob_store.get(_stored_key(upload_id, blob_store))
    if format_of(filename) != "pdf":
        return NotATableResult(upload_id=upload_id, detail="Only a scanned PDF has image pages to extract")
    try:
        _read(data, "pdf", filename=filename, sheet=None, header_row=None)
    except NeedsImageExtraction as exc:
        pages = list(exc.pages)
    except (NotATable, UnsupportedEncoding) as exc:
        return NotATableResult(upload_id=upload_id, detail=exc.detail)
    else:
        return NotATableResult(upload_id=upload_id, detail="This PDF has a text table and no image pages that "
                                                           "need the model; confirm it as it is")
    try:
        pngs = render_pages_png(data, pages, max_pages=MAX_IMAGE_PAGES)
    except NotATable as exc:
        return NotATableResult(upload_id=upload_id, detail=exc.detail)
    agent_input = TableExtractionInput(
        upload_id=upload_id, filename=filename, pages=pages,
        page_images=[base64.b64encode(png).decode("ascii") for png in pngs],
        page_hashes=[hashlib.sha256(png).hexdigest() for png in pngs])
    run = agent.run(session, agent_input)
    [proposal] = run.proposals
    table: ExtractedTable = run.output
    return TableExtracted(proposal_id=proposal.id, header=list(table.header), rows=table.rows[:PREVIEW_ROWS],
                          row_count=len(table.rows), confidence=table.confidence, notes=list(table.notes))


def _extracted(proposal: Proposal, *, upload_id: UUID, filename: str) -> tuple[Table, Manifest] | None:
    """The table of an extracted_table proposal, as the readers return one; None when the model read no table.
    InvalidUploadInput when the proposal is not one of this upload; ProposalAlreadyDecided when it is decided."""
    if proposal.kind != "extracted_table" or proposal.payload.get("upload_id") != str(upload_id):
        raise InvalidUploadInput("This extracted table is not the table of this upload")
    if proposal.status != "pending":
        raise ProposalAlreadyDecided(f"Proposal {proposal.id} is already {proposal.status}")
    read = ExtractedTable.model_validate({key: proposal.payload[key] for key in ("header", "rows", "confidence",
                                                                                  "notes")})
    if not read.header:
        return None
    header = clean_header(list(read.header))
    columns = []
    for position, name in enumerate(header):
        cells = [row[position] for row in read.rows]
        counts = Counter("empty" if cell is None or not cell.strip() else "text" for cell in cells)
        columns.append(IntakeColumn(name=name, position=position + 1, cell_types=dict(counts)))
    table = Table(header=header, rows=[list(row) for row in read.rows], columns=columns, header_row=1, skipped=[])
    manifest = Manifest(
        format="pdf", original_name=filename, encoding=None, delimiter=None, sheet=None, sheets=[], header_row=1,
        row_count=len(read.rows), columns=columns, skipped=[], pages=[int(n) for n in proposal.payload["pages"]],
        ignored_pages=[], extraction="image_pdf",
        reader=f"TableExtractorAgent, confirmed extracted table proposal {proposal.id}")
    return table, manifest


def confirm_upload(session: Session, *, upload_id: UUID, filename: str, feed_code: str, feed_name: str,
                   sheet: str | None, header_row: int | None, blob_store: BlobStore, user: User, notifier: Notifier,
                   backend_team: Sequence[str], conditions: SourceConditions | None = None,
                   description: str = "", extracted_proposal_id: UUID | None = None,
                   dropzone_root: Path | None = None) -> ConfirmResult:
    """Read the stored original with the confirmed options and create the feed from it.

    With dropzone_root the original is also copied into the feed's drop folder (task 4.4, decision D5), so the first
    scheduled load finds it. The copy is the last thing the step does; like the blobs it writes, it is an orphan
    if the caller's transaction then fails.

    An image-only PDF is confirmed with extracted_proposal_id: the table is the one the TableExtractorAgent
    proposed (extract_table), the proposal is approved by the operator in this same transaction, and the canonical
    CSV is written from its header and rows (manifest extraction "image_pdf"). A file that is read without the model
    refuses the id.

    InvalidUploadInput (422), UploadNotFound and ProposalNotFound (404), DuplicateFeedCode, DuplicateFeedSource and
    ProposalAlreadyDecided (409), NotAnApprover (403) are named errors. NotATableResult: the confirmed options find
    no table; nothing is written."""
    if not _FEED_CODE.match(feed_code):
        raise InvalidUploadInput(f"Feed code {feed_code!r} must be lower-case snake_case (letters, digits, '_')")
    if not feed_name.strip():
        raise InvalidUploadInput("The feed needs a name")
    fmt = format_of(filename)
    if fmt is None:
        raise InvalidUploadInput(_unsupported(filename).detail)

    # The file is read and parsed first: the caller's session has not been used, so no connection is held.
    original_ref = _stored_key(upload_id, blob_store)
    data = blob_store.get(original_ref)
    image_pdf = False
    try:
        table, manifest = _read(data, fmt, filename=filename, sheet=sheet, header_row=header_row)
    except NeedsImageExtraction as exc:
        if extracted_proposal_id is None:
            return NotATableResult(upload_id=upload_id, detail=f"Pages {exc.pages} are images; extract the table "
                                                               "from them first, then confirm it")
        image_pdf = True
    except (NotATable, UnsupportedEncoding) as exc:
        return NotATableResult(upload_id=upload_id, detail=exc.detail)
    if extracted_proposal_id is not None and not image_pdf:
        raise InvalidUploadInput("This file is read without the model: confirm it without an extracted table")

    source_code = f"upl_{feed_code}"
    lock_source(session, source_code)  # first in the lock order: two confirmations of one code wait for each other
    if extracted_proposal_id is not None:  # an image PDF: the others were refused above
        proposal = ProposalRepository(session).lock(extracted_proposal_id)  # ProposalNotFound; after the source lock
        read = _extracted(proposal, upload_id=upload_id, filename=filename)
        if read is None:
            return NotATableResult(upload_id=upload_id, detail="The model read no table from these pages; "
                                                               "nothing was confirmed")
        table, manifest = read
        # The operator's confirmation is the approval of the proposal (rule 1), in this transaction. A proposal
        # already decided is ProposalAlreadyDecided (409); a user who is not of the backend team, NotAnApprover.
        ProposalRepository(session).approve_my_steps(extracted_proposal_id, user=user)
    canonical = canonical_csv(table)
    manifest_bytes = manifest_json(manifest)
    feed = FeedRepository(session).create_candidate(
        code=feed_code, name=feed_name, source_code=source_code, connector="dropzone",
        source_ref=f"dropzone/{feed_code}")  # DuplicateFeedCode, DuplicateFeedSource
    fetch_id, day = uuid.uuid4(), datetime.now(UTC).date()
    stem = safe_name(filename).rsplit(".", 1)[0] or "upload"
    csv_ref = blob_key(feed_code, day, fetch_id, f"{stem}.csv")
    manifest_ref = blob_key(feed_code, day, fetch_id, "manifest.json")
    blob_store.put(csv_ref, canonical)
    blob_store.put(manifest_ref, manifest_bytes)
    SourceFetchRepository(session).record(feed_id=feed.id, fetch_id=fetch_id, outcome="fetched", blob_ref=csv_ref,
                                          size_bytes=len(canonical))
    chosen = conditions or DEFAULT_CONDITIONS
    UploadSourceRepository(session).create(
        code=source_code, feed_id=feed.id, name=feed_name, provider=f"Uploaded by {user.display_name}",
        description=description.strip() or f"Uploaded file {filename}", licence=chosen.licence,
        permitted_use=chosen.permitted_use, redistribution=chosen.redistribution, original_name=filename,
        original_ref=original_ref, manifest_ref=manifest_ref, manifest=manifest.model_dump(mode="json"),
        intake_options={"sheet": manifest.sheet, "header_row": manifest.header_row}, created_by=user.id)
    for user_id in backend_team:
        notifier.notify(recipient=user_id, subject=f"File ready to profile: {feed_code}",
                        body=f"The first file of candidate feed {feed_code} is stored as {csv_ref} "
                             f"({len(canonical)} bytes).")
    if dropzone_root is not None:
        write_to_dropzone(dropzone_root, feed_code, filename, data)
    return Uploaded(feed_id=feed.id, feed_code=feed_code, fetch_id=fetch_id, row_count=manifest.row_count,
                    columns=list(table.header))
