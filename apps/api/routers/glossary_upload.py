"""Glossary upload API (phase 7.19, T2; plan docs/plans/phase-7.19.md). Backend roles read; the glossary owner uploads and
decides.

POST /api/amaze/glossary/uploads?filename=..   the raw CSV or XLSX file is the body (application/octet-stream, no
    python-multipart). 200 uploaded (every row with its label); 413 too_large and 422 for another refused file, both a
    `file_refused` body with a reason. The same call is the "Add element" form (a one-row file).
GET  /api/amaze/glossary/uploads, .../uploads/{id}   the uploads, newest first; one with its rows and their decisions.
GET  /api/amaze/glossary/template   the header row as a CSV.
POST /api/amaze/glossary/imports/decision   {proposal_ids, decision, note}: approve or reject the selected rows. One approvals
    row each, by the person who clicks; a refused row is an item of the answer, the others are decided. 422 when a
    rejection has no note; 403 for anyone but the glossary owner.

The file is read before the session is used (no database connection is held while it is read); the upload is one
transaction (apps/api/transactions.py). No model is called.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from apps.api.deps import backend_user, get_app_settings, get_db_session, glossary_owner_user
from apps.api.routers.uploads import _body
from apps.api.transactions import in_transaction, read
from core.steps.glossary_upload import (
    FileRefused,
    ImportDecision,
    UploadPage,
    Uploaded,
    UploadSummary,
    decide_imports,
    list_uploads,
    read_glossary_file,
    record_upload,
    template_csv,
    upload_page,
)
from ports.config import Settings
from ports.identity import User

router = APIRouter(prefix="/api/amaze/glossary", tags=["glossary upload"], dependencies=[Depends(backend_user)])

DbSession = Annotated[Session, Depends(get_db_session)]
Owner = Annotated[User, Depends(glossary_owner_user)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
FileName = Annotated[str, StringConstraints(min_length=1, max_length=255)]
Text = Annotated[str, StringConstraints(max_length=500)]


@router.post("/uploads", response_model=Uploaded,
             responses={413: {"model": FileRefused}, 422: {"model": FileRefused}})
async def upload(request: Request, filename: Annotated[FileName, Query()], session: DbSession, user: Owner,
                 settings: AppSettings):
    max_bytes = settings.upload_max_mb * 1024 * 1024
    body = await _body(request, max_bytes)
    if isinstance(body, int):
        return _refused(FileRefused(reason="too_large", detail=f"The file is over the limit of {settings.upload_max_mb} MB"))
    rows = await run_in_threadpool(lambda: read_glossary_file(body, filename=filename, max_bytes=max_bytes))
    if isinstance(rows, FileRefused):
        return _refused(rows)
    sha256 = hashlib.sha256(body).hexdigest()
    return in_transaction(session, lambda: record_upload(session, rows=rows, file_name=filename, sha256=sha256, user=user))


def _refused(result: FileRefused) -> JSONResponse:
    return JSONResponse(status_code=413 if result.reason == "too_large" else 422, content=result.model_dump(mode="json"))


@router.get("/uploads", response_model=list[UploadSummary])
def uploads(session: DbSession) -> list[UploadSummary]:
    return read(session, lambda: list_uploads(session))


@router.get("/uploads/{upload_id}", response_model=UploadPage)
def upload_one(upload_id: UUID, session: DbSession) -> UploadPage:
    return read(session, lambda: upload_page(session, upload_id))


@router.get("/template")
def template() -> Response:
    return Response(content=template_csv(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="glossary_template.csv"'})


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_ids: Annotated[list[UUID], Field(min_length=1, max_length=2000)]
    decision: Literal["approved", "rejected"]
    note: Text | None = None


class DecisionResponse(BaseModel):
    items: list[ImportDecision]


@router.post("/imports/decision", response_model=DecisionResponse)
def decide(body: DecisionBody, session: DbSession, user: Owner) -> DecisionResponse:
    items = in_transaction(session, lambda: decide_imports(session, proposal_ids=body.proposal_ids, user=user,
                                                           decision=body.decision, note=body.note))
    return DecisionResponse(items=items)
