"""Data product consumption API (consumer-ui). Phase 2: search (step 17) and "Request this data"
(routing checks and step 01). The requester only; every backend role gets 403 (decision 10).

The endpoint owns the transaction: a request is committed whole or rolled back whole.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session, get_identity_port, get_request_notifier, requester_user
from core.repositories.consumer_requests import ConsumerRequestRepository
from core.repositories.errors import ConsumerRequestNotFound, RequirementNotFound, UnknownGlossaryElement
from core.repositories.requirements import RequirementRepository
from core.steps.errors import StepInputError
from core.steps.request_data import RequestResult, request_data
from core.steps.s17_discover import Discovery, discover
from ports.identity import Identity, User
from ports.notify import Notifier

router = APIRouter(prefix="/api/consumer", tags=["consumer"])

DbSession = Annotated[Session, Depends(get_db_session)]
Requester = Annotated[User, Depends(requester_user)]
IdentityPort = Annotated[Identity, Depends(get_identity_port)]

QUERY_LENGTH, PURPOSE_LENGTH, TERM_COUNT, TERM_LENGTH = 500, 2000, 20, 100
NOTIFIED_ROLES = ("backend_team", "glossary_owner")


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=QUERY_LENGTH)]
    purpose: Annotated[str, StringConstraints(max_length=PURPOSE_LENGTH)] = ""
    element_keys: list[str] = Field(default_factory=list, max_length=50)
    terms: list[Annotated[str, StringConstraints(max_length=TERM_LENGTH)]] = Field(
        default_factory=list, max_length=TERM_COUNT
    )


class RequestSummary(BaseModel):
    id: UUID
    created_at: datetime
    query: str
    outcome: str
    product_codes: list[str]
    message: str
    requirement_id: UUID | None
    requirement_status: str | None


class RequestDetail(RequestSummary):
    purpose: str
    element_keys: list[str]
    terms: list[str]
    result: dict[str, Any]


def _display_name(identity: Identity) -> Callable[[str], str]:
    def name(user_id: str) -> str:
        user = identity.get_user(user_id)
        return user.display_name if user else user_id

    return name


def _recipients(identity: Identity) -> dict[str, list[str]]:
    users = identity.list_users()
    return {role: [u.id for u in users if role in u.roles] for role in NOTIFIED_ROLES}


@router.get("/search", response_model=Discovery)
def search(session: DbSession, user: Requester, identity: IdentityPort,
           q: Annotated[str, Query(max_length=QUERY_LENGTH)] = "") -> Discovery:
    return discover(session, query=q, user_id=user.id, roles=list(user.roles), display_name=_display_name(identity))


@router.post("/requests", response_model=RequestResult, status_code=201)
def create_request(body: RequestBody, session: DbSession, user: Requester, identity: IdentityPort,
                   notifier: Annotated[Notifier, Depends(get_request_notifier)]) -> RequestResult:
    try:
        result = request_data(
            session, requester_id=user.id, roles=list(user.roles), query=body.query, purpose=body.purpose,
            element_keys=body.element_keys, terms=body.terms, notifier=notifier,
            recipients=_recipients(identity), display_name=_display_name(identity),
        )
        session.commit()
    except (StepInputError, UnknownGlossaryElement) as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except BaseException:
        session.rollback()
        raise
    return result


def _summaries(session: Session, requests) -> list[RequestDetail]:
    requirements = RequirementRepository(session)
    out = []
    for r in requests:
        status = None
        if r.requirement_id is not None:
            try:
                status = requirements.get(r.requirement_id).status
            except RequirementNotFound:
                status = None
        out.append(RequestDetail(
            id=r.id, created_at=r.created_at, query=r.query, outcome=r.outcome, product_codes=r.product_codes,
            message=r.result.get("message", ""), requirement_id=r.requirement_id, requirement_status=status,
            purpose=r.purpose, element_keys=r.element_keys, terms=r.terms, result=r.result,
        ))
    return out


@router.get("/requests", response_model=list[RequestSummary])
def my_requests(session: DbSession, user: Requester) -> list[RequestDetail]:
    return _summaries(session, ConsumerRequestRepository(session).list_for(user.id))


@router.get("/requests/{request_id}", response_model=RequestDetail)
def my_request(request_id: UUID, session: DbSession, user: Requester) -> RequestDetail:
    try:
        request = ConsumerRequestRepository(session).get_for(user.id, request_id)
    except ConsumerRequestNotFound:
        raise HTTPException(status_code=404, detail="Request not found") from None
    return _summaries(session, [request])[0]
