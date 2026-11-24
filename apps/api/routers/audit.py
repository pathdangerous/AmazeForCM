"""Phase 7.15: the monthly audit view and its CSV (plan docs/plans/phase-7.15.md, 2.4). Read only, any backend role."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.deps import backend_user, get_db_session
from apps.api.transactions import read
from core.steps.audit import AuditRow, audit_csv, audit_rows

router = APIRouter(prefix="/api/amaze", tags=["audit"], dependencies=[Depends(backend_user)])
DbSession = Annotated[Session, Depends(get_db_session)]
SHOWN = 2000  # the screen shows this many; the CSV has every row


class AuditView(BaseModel):
    month: str
    count: int  # every row of the month under the filters
    truncated: bool  # the screen shows the first SHOWN rows; the CSV has all
    rows: list[AuditRow]


def _rows(session: Session, month: str, feed: UUID | None, person: str | None) -> list[AuditRow]:
    try:
        return read(session, lambda: audit_rows(session, month=month, feed_id=feed, person=person or None))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/audit", response_model=AuditView)
def audit(month: str, session: DbSession, feed: UUID | None = None, person: str | None = None) -> AuditView:
    rows = _rows(session, month, feed, person)
    return AuditView(month=month, count=len(rows), truncated=len(rows) > SHOWN, rows=rows[:SHOWN])


@router.get("/audit.csv")
def audit_download(month: str, session: DbSession, feed: UUID | None = None, person: str | None = None) -> Response:
    body = audit_csv(_rows(session, month, feed, person))
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="audit-{month}.csv"'})
