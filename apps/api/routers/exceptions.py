"""Phase 7.14: exceptions of a load (plan docs/plans/phase-7.14.md). T1: the read-only views, for any backend role."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from apps.api.deps import backend_user, get_db_session
from apps.api.transactions import read
from core.steps.exceptions import MAX_PAGE, LoadExceptionCounts, LoadExceptions, feed_exceptions, load_exceptions

router = APIRouter(prefix="/api/amaze", tags=["exceptions"], dependencies=[Depends(backend_user)])
DbSession = Annotated[Session, Depends(get_db_session)]


@router.get("/loads/{load_id}/exceptions", response_model=LoadExceptions)
def exceptions_of_load(load_id: UUID, session: DbSession, rule: str | None = None,
                       limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = MAX_PAGE,
                       offset: Annotated[int, Query(ge=0)] = 0) -> LoadExceptions:
    return read(session, lambda: load_exceptions(session, load_id, rule=rule, limit=limit, offset=offset))


@router.get("/feeds/{feed_id}/exceptions", response_model=list[LoadExceptionCounts])
def exceptions_of_feed(feed_id: UUID, session: DbSession) -> list[LoadExceptionCounts]:
    return read(session, lambda: feed_exceptions(session, feed_id))
