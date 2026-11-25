"""Direct file intake API (phase 7.8, task 2.4; plan assumptions 16 to 20). Backend team only.

POST /api/amaze/uploads?filename=..[&sheet=..&header_row=..]  the raw file is the body (application/octet-stream,
    so no python-multipart): stored and inspected. 200 inspected or needs_image_extraction; 413 too_large;
    422 unsupported_format or not_a_table (the outcome is in the JSON body).
POST /api/amaze/uploads/{upload_id}/inspect  the stored original read again with another sheet or header row.
POST /api/amaze/uploads/{upload_id}/extract  a scanned (image-only) PDF: its pages go to the model, which proposes
    the table (task 2.3). 200 table_extracted; 404 unknown upload; 422 not_a_table or the model refused;
    503 the model did not answer.
POST /api/amaze/uploads/{upload_id}/confirm  how to read it, the feed's name and conditions: the feed is created
    as a known candidate feed with its file fetched. A scanned PDF names extracted_proposal_id: the operator's
    confirmation approves that proposal. 200 uploaded; 404 unknown upload or proposal; 409 taken feed code or a
    proposal already decided; 422 bad input or not_a_table.

POST /api/amaze/feeds/{feed_id}/files?filename=  (task 4.4) a new file of an uploaded feed, written into its drop
    folder as <UTC timestamp>_<name>. It loads nothing. 200 {stored_as}; 404 unknown feed; 409 not an uploaded feed;
    413 too_large; 422 unsupported_format.

The body is refused early when Content-Length exceeds UPLOAD_MAX_MB, and its real length is checked as it streams
in. The confirmation is one transaction (apps/api/transactions.py); the file is read before the session is used.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from agents.table_extractor import TableExtractorAgent
from apps.api.deps import (
    backend_team_user,
    get_app_settings,
    get_blob_store_port,
    get_db_session,
    get_identity_port,
    get_onboarding_kick,
    get_request_notifier,
    get_table_extractor_agent,
)
from core.domain.schedule import ScheduleInput
from core.steps.onboarding import start_session
from apps.api.transactions import in_transaction, read
from core.repositories.errors import NotADropzoneFeed, UploadNotFound
from core.repositories.feeds import FeedRepository
from core.steps.s00_intake import (
    DEFAULT_CONDITIONS,
    Inspected,
    InspectResult,
    NeedsImageExtractionResult,
    NotATableResult,
    TableExtracted,
    TooLarge,
    Uploaded,
    _unsupported,
    confirm_upload,
    extract_table,
    format_of,
    inspect_upload,
    reinspect_upload,
)
from ports.config import Settings
from ports.connectors.catalogue import SourceConditions
from ports.connectors.dropzone import write_to_dropzone
from ports.identity import Identity, User
from ports.notify import Notifier
from ports.storage import BlobStore

router = APIRouter(prefix="/api/amaze/uploads", tags=["uploads"], dependencies=[Depends(backend_team_user)])

DbSession = Annotated[Session, Depends(get_db_session)]
Operator = Annotated[User, Depends(backend_team_user)]
IdentityPort = Annotated[Identity, Depends(get_identity_port)]
RequestNotifier = Annotated[Notifier, Depends(get_request_notifier)]
Blobs = Annotated[BlobStore, Depends(get_blob_store_port)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
OnboardingKick = Annotated[object, Depends(get_onboarding_kick)]
FileName = Annotated[str, StringConstraints(min_length=1, max_length=255)]
Condition = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
FeedCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=60)]
FeedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def _reply(result: InspectResult) -> JSONResponse | InspectResult:
    """200 for a file that was read (or that needs the image path); the negative outcomes carry their status."""
    if isinstance(result, (Inspected, NeedsImageExtractionResult)):
        return result
    status = 413 if isinstance(result, TooLarge) else 422
    return JSONResponse(status_code=status, content=result.model_dump(mode="json"))


async def _body(request: Request, max_bytes: int) -> bytes | int:
    """The body, or its size when it is over the limit: by Content-Length first, then by what actually arrives."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        return int(declared)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            return size
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=Inspected | NeedsImageExtractionResult,
             responses={413: {"model": TooLarge}, 422: {"description": "unsupported_format or not_a_table"}})
async def upload(request: Request, filename: Annotated[FileName, Query()], settings: AppSettings, blobs: Blobs,
                 sheet: Annotated[str | None, Query(max_length=255)] = None,
                 header_row: Annotated[int | None, Query(ge=1)] = None):
    max_bytes = settings.upload_max_mb * 1024 * 1024
    body = await _body(request, max_bytes)
    if isinstance(body, int):
        result: InspectResult = TooLarge(size_bytes=body, max_bytes=max_bytes,
                                         detail=f"The file is over the limit of {settings.upload_max_mb} MB")
        return _reply(result)
    result = await run_in_threadpool(lambda: inspect_upload(
        body, filename=filename, blob_store=blobs, max_bytes=max_bytes, sheet=sheet, header_row=header_row))
    return _reply(result)


class InspectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: FileName
    sheet: Annotated[str, StringConstraints(max_length=255)] | None = None
    header_row: Annotated[int, Field(ge=1)] | None = None


@router.post("/{upload_id}/inspect", response_model=Inspected | NeedsImageExtractionResult,
             responses={422: {"description": "unsupported_format or not_a_table"}})
def inspect_again(upload_id: UUID, body: InspectBody, blobs: Blobs):
    try:
        result = reinspect_upload(upload_id, filename=body.filename, blob_store=blobs, sheet=body.sheet,
                                  header_row=body.header_row)
    except UploadNotFound:
        raise HTTPException(status_code=404, detail="Not found") from None
    return _reply(result)


class ExtractBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: FileName


@router.post("/{upload_id}/extract", response_model=TableExtracted,
             responses={422: {"description": "not_a_table, or the model refused", "model": NotATableResult},
                        503: {"description": "the model did not answer"}})
def extract(upload_id: UUID, body: ExtractBody, session: DbSession, blobs: Blobs,
            agent: Annotated[TableExtractorAgent, Depends(get_table_extractor_agent)]):
    """The pages of a scanned PDF go to the model; its table is proposed (an extracted_table proposal) for the
    operator to confirm. The stored file is read with no connection held, then the model is called (503 when it does
    not answer, 422 when it refuses), then the proposal is written and committed."""
    result = in_transaction(session, lambda: extract_table(
        session, upload_id=upload_id, filename=body.filename, agent=agent, blob_store=blobs))
    if isinstance(result, NotATableResult):
        return JSONResponse(status_code=422, content=result.model_dump(mode="json"))
    return result


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: FileName
    extracted_proposal_id: UUID | None = None  # an image PDF: the extracted_table proposal to confirm
    feed_code: FeedCode
    feed_name: FeedName
    sheet: Annotated[str, StringConstraints(max_length=255)] | None = None
    header_row: Annotated[int, Field(ge=1)] | None = None
    licence: Condition | None = None
    permitted_use: Condition | None = None
    redistribution: Condition | None = None
    description: Annotated[str, StringConstraints(max_length=500)] = ""
    schedule: ScheduleInput  # phase 7.21: recurring or one-time, required; written with the feed


class UploadedAndOnboarding(Uploaded):
    """Uploaded, and the onboarding session started in the same transaction (task 5.4)."""

    onboarding_id: UUID


@router.post("/{upload_id}/confirm", response_model=UploadedAndOnboarding,
             responses={422: {"description": "bad input or not_a_table"}})
def confirm(upload_id: UUID, body: ConfirmBody, session: DbSession, user: Operator, identity: IdentityPort,
            notifier: RequestNotifier, blobs: Blobs, settings: AppSettings, kick: OnboardingKick):
    conditions = SourceConditions(
        licence=body.licence or DEFAULT_CONDITIONS.licence,
        permitted_use=body.permitted_use or DEFAULT_CONDITIONS.permitted_use,
        redistribution=body.redistribution or DEFAULT_CONDITIONS.redistribution)
    team = [u.id for u in identity.list_users() if "backend_team" in u.roles]

    def work():
        result = confirm_upload(
            session, upload_id=upload_id, filename=body.filename, feed_code=body.feed_code, feed_name=body.feed_name,
            sheet=body.sheet, header_row=body.header_row, conditions=conditions, description=body.description,
            blob_store=blobs, user=user, notifier=notifier, backend_team=team,
            extracted_proposal_id=body.extracted_proposal_id, dropzone_root=settings.dropzone_root,
            schedule=body.schedule)
        if isinstance(result, NotATableResult):
            return result
        started = start_session(session, feed_id=result.feed_id, user=user)  # the profile job is queued with it
        return UploadedAndOnboarding(**result.model_dump(), onboarding_id=started.id)

    result = in_transaction(session, work)
    if isinstance(result, NotATableResult):
        return JSONResponse(status_code=422, content=result.model_dump(mode="json"))
    kick()  # after the commit: the worker sees the profile job
    return result


# ---------------------------------------------------------------- recurring files (task 4.4, decision D5)

feed_files_router = APIRouter(prefix="/api/amaze/feeds", tags=["uploads"], dependencies=[Depends(backend_team_user)])


class StoredFile(BaseModel):
    stored_as: str  # the file's name in the feed's drop folder


def _dropzone_feed_code(session: Session, feed_id: UUID) -> str:
    feed = FeedRepository(session).get(feed_id)  # FeedNotFound: 404
    if feed.connector != "dropzone":
        raise NotADropzoneFeed(f"Feed {feed.code!r} does not read a drop folder; only an uploaded feed takes new files")
    return feed.code


@feed_files_router.post("/{feed_id}/files", response_model=StoredFile,
                        responses={409: {"description": "the feed is not an uploaded feed"},
                                   413: {"model": TooLarge}, 422: {"description": "unsupported_format"}})
async def re_upload(feed_id: UUID, request: Request, filename: Annotated[FileName, Query()], session: DbSession,
                    settings: AppSettings):
    """A new file of an uploaded feed, written into its drop folder. It loads nothing: the operator then presses
    Load now, or the daily job takes the newest file of the folder."""
    code = await run_in_threadpool(lambda: read(session, lambda: _dropzone_feed_code(session, feed_id)))
    max_bytes = settings.upload_max_mb * 1024 * 1024
    if format_of(filename) is None:
        return JSONResponse(status_code=422, content=_unsupported(filename).model_dump(mode="json"))
    body = await _body(request, max_bytes)
    if isinstance(body, int):
        too_large = TooLarge(size_bytes=body, max_bytes=max_bytes,
                             detail=f"The file is over the limit of {settings.upload_max_mb} MB")
        return JSONResponse(status_code=413, content=too_large.model_dump(mode="json"))
    stored_as = await run_in_threadpool(lambda: write_to_dropzone(settings.dropzone_root, code, filename, body))
    return StoredFile(stored_as=stored_as)
