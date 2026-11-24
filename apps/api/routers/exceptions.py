"""Phase 7.14: exceptions of a load (plan docs/plans/phase-7.14.md). T1: the read-only views, for any backend role.
T2: Propose fixes (the ExceptionFixAgent: read, end the transaction, call, lock, check, write) and a fix by hand, for
a data steward. A fix is decided through POST /api/amaze/proposals/{id}/decision, like every proposal."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from agents.exception_fix import ExceptionFixAgent
from apps.api.deps import backend_user, data_steward_user, get_app_settings, get_db_session, get_exception_fix_agent
from apps.api.transactions import in_transaction, read
from core.domain.proposal import Proposal
from core.steps.exceptions import (
    MAX_PAGE,
    FixesResult,
    LoadExceptionCounts,
    LoadExceptions,
    add_manual_fix,
    exception_fix_input,
    feed_exceptions,
    load_exceptions,
    propose_fixes,
)
from ports.config import Settings
from ports.identity.base import User

router = APIRouter(prefix="/api/amaze", tags=["exceptions"], dependencies=[Depends(backend_user)])
DbSession = Annotated[Session, Depends(get_db_session)]
Steward = Annotated[User, Depends(data_steward_user)]
FixAgent = Annotated[ExceptionFixAgent, Depends(get_exception_fix_agent)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


@router.get("/loads/{load_id}/exceptions", response_model=LoadExceptions)
def exceptions_of_load(load_id: UUID, session: DbSession, rule: str | None = None,
                       limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = MAX_PAGE,
                       offset: Annotated[int, Query(ge=0)] = 0) -> LoadExceptions:
    return read(session, lambda: load_exceptions(session, load_id, rule=rule, limit=limit, offset=offset))


@router.get("/feeds/{feed_id}/exceptions", response_model=list[LoadExceptionCounts])
def exceptions_of_feed(feed_id: UUID, session: DbSession) -> list[LoadExceptionCounts]:
    return read(session, lambda: feed_exceptions(session, feed_id))


class ProposeFixesResponse(BaseModel):
    result: FixesResult
    exceptions: LoadExceptions


@router.post("/loads/{load_id}/exceptions/propose", response_model=ProposeFixesResponse)
def propose_exception_fixes(load_id: UUID, session: DbSession, user: Steward, agent: FixAgent,
                            settings: AppSettings) -> ProposeFixesResponse:
    plan = read(session, lambda: exception_fix_input(session, load_id=load_id, settings=settings))  # ends the read
    result = in_transaction(session, lambda: propose_fixes(session, agent=agent, agent_input=plan))
    return ProposeFixesResponse(result=result, exceptions=read(session, lambda: load_exceptions(session, load_id)))


class ManualFixBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_key: Annotated[str, StringConstraints(min_length=1)]
    action: Literal["transformation", "waive", "refer"]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    steps: list[dict[str, Any]] = Field(default_factory=list)  # a transformation's full steps


class ManualFixResponse(BaseModel):
    proposal: Proposal
    exceptions: LoadExceptions


@router.post("/loads/{load_id}/exceptions/fixes", response_model=ManualFixResponse)
def add_exception_fix(load_id: UUID, body: ManualFixBody, session: DbSession, user: Steward) -> ManualFixResponse:
    proposal = in_transaction(session, lambda: add_manual_fix(
        session, load_id=load_id, group_key=body.group_key, action=body.action, reason=body.reason, user=user,
        steps=body.steps or None))
    return ManualFixResponse(proposal=proposal, exceptions=read(session, lambda: load_exceptions(session, load_id)))
