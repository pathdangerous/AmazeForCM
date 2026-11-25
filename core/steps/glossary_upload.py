"""Glossary upload and the glossary owner's decision on its rows (phase 7.19, T2; plan docs/plans/phase-7.19.md).

The glossary is the base for auto-mapping, so it must be easy to fill. A CSV or XLSX file of elements
(key, name, domain, data_type, definition, aliases) is read in memory (no database connection held), every row gets a
label, and a proposal is written for each `new` and `update` row, in one savepoint with the upload record. The glossary
owner approves or rejects each (rule 1); an approved row is version 1 of a new key or the key's next version (rule 7).
No model is called anywhere here (rule 4 logs model calls; there is none), and nothing here imports an agent.

Labels, in this order (the first that applies): invalid(<reason>); duplicate_in_file; alias_conflict(<element>);
unchanged; update; new.
Typed refusals of a whole file (rule 3): file_refused with a reason; of one row of a decision: refused with a reason, and
the other rows of the same request are still decided.

Lock order: the proposal row, then the glossary advisory lock (the end of the phase 5 order).
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from core.domain.glossary import DATA_TYPES, GlossaryElement, GlossaryElementDraft
from core.repositories.configuration import MappingRepository
from core.repositories.errors import (
    GlossaryUploadNotFound,
    NotAnApprover,
    ProposalAlreadyDecided,
    ProposalNotFound,
    WrongProposalKind,
)
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.tables import GlossaryUploadRow
from core.steps.candidates import phrases
from core.steps.errors import needs_a_note
from core.steps.matching import words
from core.steps.result import StepResult
from ports.identity.base import User

COLUMNS = ("key", "name", "domain", "data_type", "definition", "aliases")
MAX_ROWS = 2000  # data rows in one file; a bigger file is split
SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
Label = Literal["new", "update", "unchanged", "invalid", "alias_conflict", "duplicate_in_file"]
LABELS: tuple[str, ...] = ("new", "update", "unchanged", "invalid", "alias_conflict", "duplicate_in_file")
DIFFERS = ("name", "domain", "data_type", "definition", "aliases")  # an update names the fields that differ, in this order


# ---------------------------------------------------------------- reading the file (no database)


class FileRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    row: int  # the n-th data row of the file (blank rows are not counted)
    values: dict[str, str]  # each of COLUMNS, stripped


class FileRefused(StepResult):
    outcome: Literal["file_refused"] = "file_refused"
    reason: Literal["too_large", "unsupported_format", "unreadable", "missing_columns", "no_rows", "too_many_rows"]
    detail: str


def template_csv() -> str:
    return ",".join(COLUMNS) + "\r\n"


def _cells(table: list[list[str]], max_rows: int) -> list[FileRow] | FileRefused:
    table = [r for r in table if any(c.strip() for c in r)]
    if not table:
        return FileRefused(reason="no_rows", detail="The file has no rows")
    header = [c.strip().lower() for c in table[0]]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        return FileRefused(reason="missing_columns",
                           detail=f"The file has no column {', '.join(missing)}; it needs {', '.join(COLUMNS)}")
    body = table[1:]
    if not body:
        return FileRefused(reason="no_rows", detail="The file has a header row and no rows")
    if len(body) > max_rows:
        return FileRefused(reason="too_many_rows", detail=f"The file has {len(body)} rows; at most {max_rows} at a time")
    at = {c: header.index(c) for c in COLUMNS}
    return [FileRow(row=n, values={c: (r[at[c]].strip() if at[c] < len(r) else "") for c in COLUMNS})
            for n, r in enumerate(body, start=1)]


def _csv_table(data: bytes) -> list[list[str]] | FileRefused:
    try:
        return list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))
    except (UnicodeDecodeError, csv.Error):
        return FileRefused(reason="unreadable", detail="The file is not UTF-8 text in comma-separated form")


def _xlsx_table(data: bytes, max_rows: int) -> list[list[str]] | FileRefused:
    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        table: list[list[str]] = []
        for scanned, values in enumerate(book.worksheets[0].iter_rows(values_only=True), start=1):
            if scanned > max_rows * 10 + 10:  # a sheet of blank formatted rows must not run on for ever
                break
            table.append(["" if v is None else str(v) for v in values])
        book.close()
        return table
    except Exception:  # noqa: BLE001 (openpyxl raises many types for a file that is not a workbook)
        return FileRefused(reason="unreadable", detail="The file is not an Excel workbook that can be read")


def read_glossary_file(data: bytes, *, filename: str, max_bytes: int, max_rows: int = MAX_ROWS) -> list[FileRow] | FileRefused:
    """Pure: the rows of a CSV or XLSX file, or the reason it is refused. No database."""
    if len(data) > max_bytes:
        return FileRefused(reason="too_large", detail=f"The file is {len(data)} bytes; the limit is {max_bytes}")
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if extension not in ("csv", "xlsx"):
        return FileRefused(reason="unsupported_format", detail="Upload a .csv or .xlsx file")
    table = _csv_table(data) if extension == "csv" else _xlsx_table(data, max_rows)
    return table if isinstance(table, FileRefused) else _cells(table, max_rows)


# ---------------------------------------------------------------- labelling


class UploadRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    row: int
    key: str
    label: Label
    detail: str | None = None  # invalid: the reason; alias_conflict: the element that owns the phrase
    changes: list[str] = Field(default_factory=list)  # update: the fields that differ
    used_by_mappings: int | None = None  # an existing element: how many mappings name it
    element: dict[str, Any] | None = None  # the parsed element of a new, update or unchanged row
    proposal_id: UUID | None = None  # new and update rows only


def _invalid(values: dict[str, str], domains: set[str]) -> str | None:
    missing = [c for c in COLUMNS[:5] if not values[c]]
    if missing:
        return f"missing field: {', '.join(missing)}"
    if not SNAKE.fullmatch(values["key"]):
        return f"bad key {values['key']!r}: lower-case letters, digits and _, starting with a letter"
    aliases = _aliases(values["aliases"])
    for alias in aliases:
        if not SNAKE.fullmatch(alias) or alias == values["key"] or aliases.count(alias) > 1:
            return f"bad alias {alias!r}: lower-case letters, digits and _, not the key, each once"
    if values["data_type"] not in DATA_TYPES:
        return f"unknown data_type {values['data_type']!r}: one of {', '.join(DATA_TYPES)}"
    if values["domain"] not in domains:
        return f"unknown domain {values['domain']!r}: one of {', '.join(sorted(domains))}"
    return None


def _aliases(cell: str) -> list[str]:
    return [a.strip() for a in cell.split(";") if a.strip()]


def label_rows(rows: Sequence[FileRow], elements: Sequence[GlossaryElement], *, pending_keys: set[str],
               uses: dict[str, int]) -> list[UploadRow]:
    """Read only. Each row's label (see the module docstring). A phrase (key, name or alias, by words) another current
    element owns, or a row of this file that is getting a proposal owns, is an alias_conflict."""
    current = {e.key: e for e in elements}
    domains = {e.domain for e in elements}
    owners: dict[tuple[str, ...], str] = {}
    for e in elements:
        for phrase in phrases(e):
            owners.setdefault(phrase, e.key)
    seen: set[str] = set()
    out: list[UploadRow] = []
    for r in rows:
        v = r.values
        key, found = v["key"], current.get(v["key"])
        uses_of = uses.get(key, 0) if found else None
        reason = _invalid(v, domains) or (f"{key} already has a pending row from an earlier upload; decide that first"
                                          if key in pending_keys else None)
        if reason:
            out.append(UploadRow(row=r.row, key=key, label="invalid", detail=reason))
        elif key in seen:
            out.append(UploadRow(row=r.row, key=key, label="duplicate_in_file",
                                 detail=f"{key} is in an earlier row of this file"))
        else:
            element = {"key": key, "name": v["name"], "domain": v["domain"], "data_type": v["data_type"],
                       "definition": v["definition"], "aliases": _aliases(v["aliases"]),
                       "unit": found.unit if found else None}
            wanted = {words(p): p for p in (key, v["name"], *element["aliases"])}
            clash = sorted({owners[w] for w in wanted if w in owners and owners[w] != key})
            if clash:
                out.append(UploadRow(row=r.row, key=key, label="alias_conflict", detail=clash[0]))
            elif found is None:
                out.append(_proposed(r.row, key, "new", element, None, []))
            else:
                changes = [f for f in DIFFERS if (set(element[f]) != set(found.aliases) if f == "aliases"
                                                  else element[f] != getattr(found, f))]
                out.append(_proposed(r.row, key, "update", element, uses_of, changes) if changes else
                           UploadRow(row=r.row, key=key, label="unchanged", used_by_mappings=uses_of, element=element))
            if out[-1].proposal_id:
                for w in wanted:
                    owners.setdefault(w, key)
        if key:
            seen.add(key)
    return out


def _proposed(row: int, key: str, label: Label, element: dict[str, Any], uses_of: int | None,
              changes: list[str]) -> UploadRow:
    return UploadRow(row=row, key=key, label=label, changes=changes, used_by_mappings=uses_of, element=element,
                     proposal_id=uuid.uuid4())


# ---------------------------------------------------------------- writing the upload


class Uploaded(StepResult):
    outcome: Literal["uploaded"] = "uploaded"
    upload_id: UUID
    file_name: str
    counts: dict[str, int]
    rows: list[UploadRow]
    proposals: int


def _summary(row: UploadRow) -> str:
    if row.label == "new":
        return f"New element {row.key}"
    return f"New version of {row.key}: " + ", ".join(row.changes)


def record_upload(session: Session, *, rows: Sequence[FileRow], file_name: str, sha256: str, user: User) -> Uploaded:
    """The glossary owner's upload: label every row under the glossary lock, then write the upload record and one
    proposal per new or update row in one savepoint (all or nothing). The caller owns the transaction."""
    if "glossary_owner" not in user.roles:
        raise NotAnApprover(f"Only a glossary_owner uploads a glossary file; {user.id} has {user.roles}")
    glossary, proposals = GlossaryRepository(session), ProposalRepository(session)
    glossary.lock()  # the labels are read under it, so they cannot go stale before the rows are written
    pending = {p.payload.get("key") for p in proposals.list(status="pending", kind="glossary_import")}
    labelled = label_rows(rows, glossary.list_current(), pending_keys=pending,
                          uses=MappingRepository(session).uses_by_element())
    upload_id, counts = uuid.uuid4(), dict(Counter(r.label for r in labelled))
    with session.begin_nested():
        session.add(GlossaryUploadRow(id=upload_id, uploaded_by=user.id, file_name=file_name, file_sha256=sha256,
                                      counts=counts, rows=[r.model_dump(mode="json") for r in labelled]))
        session.flush()
        for r in (r for r in labelled if r.proposal_id):
            base = glossary.get_current(r.key).version if r.label == "update" else None
            proposals.create_glossary_import(
                proposal_id=r.proposal_id, author=user.id,
                payload={"upload_id": str(upload_id), "row": r.row, "label": r.label, "key": r.key, "element": r.element,
                         "base_version": base, "used_by_mappings": r.used_by_mappings or 0, "changes": r.changes,
                         "summary": _summary(r)})
    return Uploaded(upload_id=upload_id, file_name=file_name, counts=counts, rows=labelled,
                    proposals=sum(1 for r in labelled if r.proposal_id))


# ---------------------------------------------------------------- reading uploads (read only)


class UploadSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    created_at: datetime
    uploaded_by: str
    file_name: str
    counts: dict[str, int]


class PageRow(UploadRow):
    status: Literal["pending", "approved", "rejected"] | None = None  # None: no proposal was made
    decided_by: str | None = None
    note: str | None = None


class UploadPage(UploadSummary):
    rows: list[PageRow]


def list_uploads(session: Session, limit: int = 50) -> list[UploadSummary]:
    query = select(GlossaryUploadRow).order_by(GlossaryUploadRow.created_at.desc(), GlossaryUploadRow.id).limit(limit)
    return [UploadSummary(id=u.id, created_at=u.created_at, uploaded_by=u.uploaded_by, file_name=u.file_name,
                          counts=u.counts) for u in session.scalars(query)]


def upload_page(session: Session, upload_id: UUID) -> UploadPage:
    upload = session.get(GlossaryUploadRow, upload_id)
    if upload is None:
        raise GlossaryUploadNotFound(f"No glossary upload with id {upload_id}")
    repo = ProposalRepository(session)
    rows: list[PageRow] = []
    for raw in upload.rows:
        row = UploadRow.model_validate(raw)
        status = decided_by = note = None
        if row.proposal_id:
            status = repo.get(row.proposal_id).status
            decision = repo.approvals(row.proposal_id)
            decided_by, note = (decision[-1].decided_by, decision[-1].note) if decision else (None, None)
        rows.append(PageRow(**row.model_dump(), status=status, decided_by=decided_by, note=note))
    return UploadPage(id=upload.id, created_at=upload.created_at, uploaded_by=upload.uploaded_by,
                      file_name=upload.file_name, counts=upload.counts, rows=rows)


# ---------------------------------------------------------------- the glossary owner's decisions


class ImportApproved(StepResult):
    outcome: Literal["approved"] = "approved"
    proposal_id: UUID
    element: GlossaryElement


class ImportRejected(StepResult):
    outcome: Literal["rejected"] = "rejected"
    proposal_id: UUID


class ImportRefused(StepResult):
    """Nobody decided: the row stays pending and the others of the same request are still decided."""

    outcome: Literal["refused"] = "refused"
    proposal_id: UUID
    reason: Literal["alias_conflict", "base_changed", "key_taken", "already_decided", "unknown_proposal",
                    "not_an_import", "database_refused"]
    detail: str


ImportDecision = Annotated[ImportApproved | ImportRejected | ImportRefused, Field(discriminator="outcome")]


class _Refused(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason


def _decide_one(session: Session, proposal_id: UUID, *, user: User, decision: str, note: str | None) -> ImportDecision:
    proposals = ProposalRepository(session)
    proposal = proposals.lock(proposal_id)  # ProposalNotFound
    if proposal.kind != "glossary_import":
        raise WrongProposalKind(f"Proposal {proposal_id} is a {proposal.kind} proposal, not glossary_import")
    if proposal.status != "pending":
        raise ProposalAlreadyDecided(f"Proposal {proposal_id} is already {proposal.status}")
    if decision == "rejected":
        proposals.record_decision(proposal_id, user=user, decision="rejected", note=note)
        return ImportRejected(proposal_id=proposal_id)
    glossary = GlossaryRepository(session)
    glossary.lock()
    payload = proposal.payload
    draft = GlossaryElementDraft(**payload["element"])
    if payload["label"] == "new":
        if glossary.exists(draft.key):
            raise _Refused("key_taken", f"{draft.key} became a glossary element after the file was uploaded")
    elif glossary.get_current(draft.key).version != payload["base_version"]:
        raise _Refused("base_changed", f"{draft.key} changed after the file was uploaded; upload it again")
    taken = glossary.phrases_taken(draft)
    if taken:
        raise _Refused("alias_conflict", "Another element already owns " + ", ".join(f"{p} ({k})" for p, k in taken))
    proposals.record_decision(proposal_id, user=user, decision="approved", note=note)
    return ImportApproved(proposal_id=proposal_id, element=glossary.add_import(proposal_id=proposal_id, created_by=user.id))


def decide_imports(session: Session, *, proposal_ids: Sequence[UUID | str], user: User, decision: Literal["approved", "rejected"],
                   note: str | None) -> list[ImportDecision]:
    """Approve or reject the selected rows: one approvals row each, by the person who clicks. Each row runs in its own
    savepoint, so a refused row (an alias another element holds, an element that changed since the upload) leaves the
    others decided. NotAnApprover and NoteNeeded refuse the whole request."""
    if "glossary_owner" not in user.roles:
        raise NotAnApprover(f"Deciding glossary rows needs role glossary_owner; {user.id} has {user.roles}")
    needs_a_note(decision, note)
    out: list[ImportDecision] = []
    for raw in proposal_ids:
        pid = UUID(str(raw))
        try:
            with session.begin_nested():
                out.append(_decide_one(session, pid, user=user, decision=decision, note=note))
        except _Refused as refused:
            out.append(ImportRefused(proposal_id=pid, reason=refused.reason, detail=str(refused)))  # type: ignore[arg-type]
        except ProposalNotFound as error:
            out.append(ImportRefused(proposal_id=pid, reason="unknown_proposal", detail=str(error)))
        except ProposalAlreadyDecided as error:
            out.append(ImportRefused(proposal_id=pid, reason="already_decided", detail=str(error)))
        except WrongProposalKind as error:
            out.append(ImportRefused(proposal_id=pid, reason="not_an_import", detail=str(error)))
        except DBAPIError as error:  # the database's own guard said no (migration 0038)
            out.append(ImportRefused(proposal_id=pid, reason="database_refused", detail=str(error.orig).splitlines()[0]))
    return out
