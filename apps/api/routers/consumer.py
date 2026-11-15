"""Data product consumption API (consumer-ui). Phase 2: search (step 17) and "Request this data"
(routing checks and step 01). The requester only; every backend role gets 403 (decision 10).

The endpoint owns the transaction: a request is committed whole or rolled back whole.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from agents.discovery_assistant import DiscoveryAssistant
from agents.intent import IntentAgent
from agents.source_discovery import SourceDiscoveryAgent
from apps.api.deps import (
    get_app_settings,
    get_blob_store_port,
    get_connector_factory,
    get_db_session,
    get_discovery_assistant,
    get_identity_port,
    get_intent_agent,
    get_request_notifier,
    get_source_catalogue,
    get_source_discovery_agent,
    requester_user,
)
from apps.api.transactions import in_transaction as _in_transaction
from apps.api.transactions import read as _read
from core.domain.requirement import Requirement
from core.repositories.consumer_requests import ConsumerRequestRepository
from core.repositories.errors import ConsumerRequestNotFound, RequirementNotFound, UnknownGlossaryElement
from core.repositories.feeds import FeedRepository
from core.repositories.requirements import RequirementRepository
from core.steps.errors import StepInputError
from core.steps.known_source import KnownSourceResult, find_known_source
from core.steps.release import Released, release_source
from core.steps.request_data import RequestResult, request_data
from core.steps.requirement_page import RequirementPage, approval_missing_message, requirement_page
from core.steps.s1_scope import ScopeProposed, ScopeResult, decide_scope, record_scope, scope_input
from core.steps.s2_sources import NoFit, SourcesResult, record_sources, sources_input
from core.steps.s3_pick import ApprovalMissing, Declined, PickResult, SourcePicked, decline_source, pick_source
from core.steps.s4_fetch import FetchResult, fetch_file, fetch_plan, record_fetch
from core.steps.s17_discover import Assistance, Discovery, assist_input, discover, record_assist
from ports.config import Settings
from ports.connectors import SourceConnector
from ports.connectors.catalogue import SourceCatalogue, SourceConditions
from ports.identity import Identity, User
from ports.notify import Notifier
from ports.storage import BlobStore

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


class AssistBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: Annotated[str, StringConstraints(max_length=QUERY_LENGTH)]


@router.post("/search/assist", response_model=Assistance)
def search_assist(body: AssistBody, session: DbSession, user: Requester, identity: IdentityPort,
                  assistant: Annotated[DiscoveryAssistant, Depends(get_discovery_assistant)]) -> Assistance:
    """The read-only DiscoveryAssistant beside the deterministic search. It changes nothing; its call is
    logged in proposal_log (rule 4), so this endpoint commits that one row."""
    agent_input = _read(session, lambda: assist_input(session, query=body.q))
    return _in_transaction(session, lambda: record_assist(
        session, agent=assistant, agent_input=agent_input, user_id=user.id, roles=list(user.roles),
        display_name=_display_name(identity)))


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


# ---- phase 3a: the custom data product (requirement page, Find a source)

Catalogue = Annotated[SourceCatalogue, Depends(get_source_catalogue)]


AppSettings = Annotated[Settings, Depends(get_app_settings)]
RequestNotifier = Annotated[Notifier, Depends(get_request_notifier)]
Blobs = Annotated[BlobStore, Depends(get_blob_store_port)]
Connectors = Annotated[Callable[[str], SourceConnector], Depends(get_connector_factory)]
ScopeAgent = Annotated[IntentAgent, Depends(get_intent_agent)]
ListAgent = Annotated[SourceDiscoveryAgent, Depends(get_source_discovery_agent)]


class FindSourceResponse(BaseModel):
    check: KnownSourceResult
    fetch: FetchResult | None
    # 3b: with no known source, S1's typed result: the proposed scope, or no_fit (no channel to search).
    # None for a known source (independent review, fix 6).
    scope: ScopeProposed | NoFit | None
    message: str
    requirement: RequirementPage


def _own_requirement(session: Session, user: User, requirement_id: UUID) -> Requirement:
    """The caller's own requirement. Another requester's is "not found" too, so its existence is hidden."""
    try:
        requirement = RequirementRepository(session).get(requirement_id)
    except RequirementNotFound:
        requirement = None
    if requirement is None or requirement.requester_id != user.id:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return requirement


def _first_fetch(session: Session, feed_id: UUID, *, identity: Identity, catalogue: SourceCatalogue,
                 notifier: Notifier, blob_store: BlobStore, connector_factory: Callable[[str], SourceConnector],
                 settings: Settings):
    """The first fetch after a selection, in its own transaction: the selection is committed first and
    stands if the fetch gives source_down (assumption 13). Read, end the transaction, fetch with no
    connection held, then lock the feed and write (review fix 3)."""
    plan = _read(session, lambda: fetch_plan(session, feed_id=feed_id))
    attempt = fetch_file(plan, connector_factory=connector_factory, blob_store=blob_store)
    return _in_transaction(session, lambda: record_fetch(
        session, attempt, notifier=notifier, backend_team=_recipients(identity)["backend_team"],
        catalogue=catalogue, release_after_days=settings.source_down_release_days))


def _page(session: Session, requirement_id: UUID, catalogue: SourceCatalogue, settings: Settings) -> RequirementPage:
    return requirement_page(session, RequirementRepository(session).get(requirement_id), catalogue,
                            settings.source_down_release_days)


@router.get("/requirements/{requirement_id}", response_model=RequirementPage)
def requirement_detail(requirement_id: UUID, session: DbSession, user: Requester, catalogue: Catalogue,
                       settings: AppSettings) -> RequirementPage:
    _own_requirement(session, user, requirement_id)
    return _page(session, requirement_id, catalogue, settings)


@router.post("/requirements/{requirement_id}/find-source", response_model=FindSourceResponse)
def find_source(requirement_id: UUID, session: DbSession, user: Requester, identity: IdentityPort,
                catalogue: Catalogue, notifier: RequestNotifier, blob_store: Blobs, connector_factory: Connectors,
                settings: AppSettings, intent_agent: ScopeAgent) -> FindSourceResponse:
    """Known source?: a known source is selected, then fetched in its own transaction. With no known
    source, S1 proposes a scope in the same transaction (one commit, whichever branch ran)."""
    _own_requirement(session, user, requirement_id)
    found = _in_transaction(session, lambda: find_known_source(session, requirement_id=requirement_id,
                                                               catalogue=catalogue))
    scope = None
    if found.feed is None:  # no_known_source wrote nothing: read S1's input, then the model call, then write
        agent_input = _read(session, lambda: scope_input(session, requirement_id=requirement_id, catalogue=catalogue))
        scope = _in_transaction(session, lambda: record_scope(
            session, agent=intent_agent, agent_input=agent_input, notifier=notifier,
            backend_team=_recipients(identity)["backend_team"]))
    fetch = None
    if found.feed is not None:
        fetch = _first_fetch(session, found.feed.id, identity=identity, catalogue=catalogue, notifier=notifier,
                             blob_store=blob_store, connector_factory=connector_factory, settings=settings)
    page = _page(session, requirement_id, catalogue, settings)
    return FindSourceResponse(check=found.check, fetch=fetch, scope=scope, message=page.message, requirement=page)


# ---- phase 3b: scope (S1), sources (S2), pick and decline (S3), release


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Annotated[str, StringConstraints(max_length=PURPOSE_LENGTH)] = ""


class DecisionBody(NoteBody):
    decision: Literal["approved", "rejected"]


class PickBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    accepted_conditions: SourceConditions | None = None  # exactly the conditions shown (assumption 56)


class DeclineBody(NoteBody):
    proposal_id: UUID


class ScopeResponse(BaseModel):
    result: ScopeProposed | NoFit  # no_fit: the proposed scope named no channel (review fix 5; independent review, fix 6)
    message: str
    requirement: RequirementPage


class ScopeDecisionResponse(BaseModel):
    result: ScopeResult
    message: str
    requirement: RequirementPage


class SourcesResponse(BaseModel):
    result: SourcesResult
    message: str
    requirement: RequirementPage


class PickResponse(BaseModel):
    result: PickResult
    fetch: FetchResult | None
    message: str
    requirement: RequirementPage


class DeclineResponse(BaseModel):
    result: Declined
    message: str
    requirement: RequirementPage


class ReleaseResponse(BaseModel):
    result: Released
    message: str
    requirement: RequirementPage


@router.post("/requirements/{requirement_id}/scope", response_model=ScopeResponse)
def ask_for_scope(requirement_id: UUID, body: NoteBody, session: DbSession, user: Requester, catalogue: Catalogue,
                  settings: AppSettings, intent_agent: ScopeAgent, identity: IdentityPort,
                  notifier: RequestNotifier) -> ScopeResponse:
    """S1 again, with the requester's note, after scope_not_confirmed or no_source_found."""
    _own_requirement(session, user, requirement_id)
    agent_input = _read(session, lambda: scope_input(session, requirement_id=requirement_id, catalogue=catalogue,
                                                     note=body.note, again=True))
    result = _in_transaction(session, lambda: record_scope(
        session, agent=intent_agent, agent_input=agent_input, again=True, notifier=notifier,
        backend_team=_recipients(identity)["backend_team"]))
    page = _page(session, requirement_id, catalogue, settings)
    return ScopeResponse(result=result, message=page.message, requirement=page)


@router.post("/requirements/{requirement_id}/scope/decision", response_model=ScopeDecisionResponse)
def decide_on_scope(requirement_id: UUID, body: DecisionBody, session: DbSession, user: Requester,
                    catalogue: Catalogue, settings: AppSettings) -> ScopeDecisionResponse:
    _own_requirement(session, user, requirement_id)
    result = _in_transaction(session, lambda: decide_scope(
        session, requirement_id=requirement_id, user=user, decision=body.decision, note=body.note))
    page = _page(session, requirement_id, catalogue, settings)
    return ScopeDecisionResponse(result=result, message=page.message, requirement=page)


@router.post("/requirements/{requirement_id}/sources", response_model=SourcesResponse)
def find_sources(requirement_id: UUID, session: DbSession, user: Requester, identity: IdentityPort,
                 catalogue: Catalogue, settings: AppSettings, notifier: RequestNotifier,
                 list_agent: ListAgent) -> SourcesResponse:
    _own_requirement(session, user, requirement_id)
    agent_input = _read(session, lambda: sources_input(session, requirement_id=requirement_id, catalogue=catalogue))
    result = _in_transaction(session, lambda: record_sources(
        session, requirement_id=requirement_id, agent=list_agent, agent_input=agent_input, catalogue=catalogue,
        notifier=notifier, backend_team=_recipients(identity)["backend_team"]))
    page = _page(session, requirement_id, catalogue, settings)
    return SourcesResponse(result=result, message=page.message, requirement=page)


@router.post("/requirements/{requirement_id}/pick", response_model=PickResponse)
def pick(requirement_id: UUID, body: PickBody, session: DbSession, user: Requester, identity: IdentityPort,
         catalogue: Catalogue, settings: AppSettings, notifier: RequestNotifier, blob_store: Blobs,
         connector_factory: Connectors) -> PickResponse:
    """S3. source_picked is committed, then the first fetch runs in its own transaction. approval_missing
    writes nothing."""
    _own_requirement(session, user, requirement_id)
    result = _in_transaction(session, lambda: pick_source(
        session, requirement_id=requirement_id, user=user, proposal_id=body.proposal_id,
        accepted_conditions=body.accepted_conditions, catalogue=catalogue))
    fetch = None
    if isinstance(result, SourcePicked):
        feed = FeedRepository(session).get_by_code(result.feed_code)
        fetch = _first_fetch(session, feed.id, identity=identity, catalogue=catalogue, notifier=notifier,
                             blob_store=blob_store, connector_factory=connector_factory, settings=settings)
    page = _page(session, requirement_id, catalogue, settings)
    message = approval_missing_message(result.source.name) if isinstance(result, ApprovalMissing) else page.message
    return PickResponse(result=result, fetch=fetch, message=message, requirement=page)


@router.post("/requirements/{requirement_id}/decline", response_model=DeclineResponse)
def decline(requirement_id: UUID, body: DeclineBody, session: DbSession, user: Requester, catalogue: Catalogue,
            settings: AppSettings) -> DeclineResponse:
    _own_requirement(session, user, requirement_id)
    result = _in_transaction(session, lambda: decline_source(
        session, requirement_id=requirement_id, user=user, proposal_id=body.proposal_id, note=body.note,
        catalogue=catalogue))
    page = _page(session, requirement_id, catalogue, settings)
    return DeclineResponse(result=result, message=page.message, requirement=page)


@router.post("/requirements/{requirement_id}/release", response_model=ReleaseResponse)
def release(requirement_id: UUID, session: DbSession, user: Requester, identity: IdentityPort, catalogue: Catalogue,
            settings: AppSettings, notifier: RequestNotifier) -> ReleaseResponse:
    """The requester releases the selected source (assumption 52); 409 unless source_selected."""
    _own_requirement(session, user, requirement_id)
    result = _in_transaction(session, lambda: release_source(
        session, requirement_id=requirement_id, reason="requester_asked", user=user, catalogue=catalogue,
        notifier=notifier, backend_team=_recipients(identity)["backend_team"]))
    page = _page(session, requirement_id, catalogue, settings)
    return ReleaseResponse(result=result, message=page.message, requirement=page)
