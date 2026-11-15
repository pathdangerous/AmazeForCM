"""Amaze platform API (backend team). Phase 1: read the glossary and the feed registry. Phase 4: steps 02 to
04 on one feed (plan, section 9): the feed page and the data owners for any backend role; profile,
summary, sign-off and register for the backend team only.

Each step is one transaction (apps/api/transactions.py). Step 02 reads, ends the transaction, runs DuckDB
with no connection held, then writes; the summary reads, ends the transaction, calls the model, then writes.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from agents.profiler import ProfilerAgent
from apps.api.deps import (
    backend_team_user,
    backend_user,
    get_blob_store_port,
    get_db_session,
    get_identity_port,
    get_profiler_agent,
    get_request_notifier,
    get_source_catalogue,
)
from apps.api.transactions import in_transaction, read
from core.domain.feed import Feed, FeedStage
from core.domain.glossary import GlossaryElement
from core.repositories.errors import UnknownGlossaryElement
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.steps.feed_page import FeedPage, feed_page
from core.steps.s02_profile import ProfileResult, ProfileSummary, profile_plan, record_profile, record_summary, \
    run_profile, summary_input
from core.steps.s03_signoff import SignoffResult, sign_off
from core.steps.s04_register import NoOwner, Registered, register_feed
from ports.connectors.catalogue import SourceCatalogue
from ports.identity import Identity, User
from ports.notify import Notifier
from ports.storage import BlobStore

router = APIRouter(prefix="/api/amaze", tags=["amaze"], dependencies=[Depends(backend_user)])

DbSession = Annotated[Session, Depends(get_db_session)]
BackendTeam = Annotated[User, Depends(backend_team_user)]
Catalogue = Annotated[SourceCatalogue, Depends(get_source_catalogue)]
IdentityPort = Annotated[Identity, Depends(get_identity_port)]
RequestNotifier = Annotated[Notifier, Depends(get_request_notifier)]
Blobs = Annotated[BlobStore, Depends(get_blob_store_port)]
Profiler = Annotated[ProfilerAgent, Depends(get_profiler_agent)]
Text = Annotated[str, StringConstraints(max_length=500)]


class DomainCount(BaseModel):
    domain: str
    label: str
    count: int


class ElementDetail(BaseModel):
    element: GlossaryElement
    history: list[GlossaryElement]


@router.get("/glossary/domains", response_model=list[DomainCount])
def glossary_domains(session: DbSession) -> list[DomainCount]:
    return [
        DomainCount(domain=domain, label=domain.replace("_", " ").capitalize(), count=count)
        for domain, count in GlossaryRepository(session).domains()
    ]


@router.get("/glossary/elements", response_model=list[GlossaryElement])
def glossary_elements(session: DbSession, domain: str | None = None, q: str | None = None) -> list[GlossaryElement]:
    return GlossaryRepository(session).list_current(domain=domain, q=q)


@router.get("/glossary/elements/{key}", response_model=ElementDetail)
def glossary_element(key: str, session: DbSession) -> ElementDetail:
    repo = GlossaryRepository(session)
    try:
        return ElementDetail(element=repo.get_current(key), history=repo.history(key))
    except UnknownGlossaryElement as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/feeds", response_model=list[Feed])
def feeds(session: DbSession, stage: FeedStage | None = None) -> list[Feed]:
    return FeedRepository(session).list(stage=stage)


# ---- phase 4: steps 02 to 04


class Owner(BaseModel):
    id: str
    display_name: str


class ProfileResponse(BaseModel):
    result: ProfileResult
    feed: FeedPage


class SummaryResponse(BaseModel):
    summary: ProfileSummary
    feed: FeedPage


class SignoffBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["meets", "meets_with_gaps", "rejected"]
    gaps: list[Text] = Field(default_factory=list, max_length=20)
    reasons: list[Text] = Field(default_factory=list, max_length=20)


class SignoffResponse(BaseModel):
    result: SignoffResult
    feed: FeedPage


class RegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: Text | None = None
    visibility: Literal["open", "restricted"]


class RegisterResponse(BaseModel):
    result: Registered | NoOwner = Field(discriminator="outcome")
    feed: FeedPage


def _page(session: Session, feed_id: UUID, catalogue: SourceCatalogue) -> FeedPage:
    return read(session, lambda: feed_page(session, FeedRepository(session).get(feed_id), catalogue))


def _backend_team(identity: Identity) -> list[str]:
    return [u.id for u in identity.list_users() if "backend_team" in u.roles]


@router.get("/feeds/{feed_id}", response_model=FeedPage)
def feed_detail(feed_id: UUID, session: DbSession, catalogue: Catalogue) -> FeedPage:
    return _page(session, feed_id, catalogue)


@router.get("/owners", response_model=list[Owner])
def owners(identity: IdentityPort) -> list[Owner]:
    return [Owner(id=u.id, display_name=u.display_name) for u in identity.list_users() if "data_owner" in u.roles]


@router.post("/feeds/{feed_id}/profile", response_model=ProfileResponse)
def profile(feed_id: UUID, session: DbSession, user: BackendTeam, catalogue: Catalogue, blobs: Blobs) -> ProfileResponse:
    plan = read(session, lambda: profile_plan(session, feed_id=feed_id, catalogue=catalogue))
    attempt = run_profile(plan, blob_store=blobs)  # no connection held while DuckDB runs
    result = in_transaction(session, lambda: record_profile(session, attempt, profiled_by=user.id))
    return ProfileResponse(result=result, feed=_page(session, feed_id, catalogue))


@router.post("/feeds/{feed_id}/profile/summary", response_model=SummaryResponse)
def summarise(feed_id: UUID, session: DbSession, user: BackendTeam, catalogue: Catalogue,
              agent: Profiler) -> SummaryResponse:
    agent_input = read(session, lambda: summary_input(session, feed_id=feed_id))
    summary = in_transaction(session, lambda: record_summary(session, agent=agent, agent_input=agent_input))
    return SummaryResponse(summary=summary, feed=_page(session, feed_id, catalogue))


@router.post("/feeds/{feed_id}/signoff", response_model=SignoffResponse)
def signoff(feed_id: UUID, body: SignoffBody, session: DbSession, user: BackendTeam, catalogue: Catalogue,
            identity: IdentityPort, notifier: RequestNotifier) -> SignoffResponse:
    result = in_transaction(session, lambda: sign_off(
        session, feed_id=feed_id, user=user, outcome=body.outcome, gaps=body.gaps, reasons=body.reasons,
        catalogue=catalogue, notifier=notifier, backend_team=_backend_team(identity)))
    return SignoffResponse(result=result, feed=_page(session, feed_id, catalogue))


@router.post("/feeds/{feed_id}/register", response_model=RegisterResponse)
def register(feed_id: UUID, body: RegisterBody, session: DbSession, user: BackendTeam, catalogue: Catalogue,
             identity: IdentityPort, notifier: RequestNotifier) -> RegisterResponse:
    result = in_transaction(session, lambda: register_feed(
        session, feed_id=feed_id, user=user, owner_id=body.owner_id, visibility=body.visibility,
        catalogue=catalogue, identity=identity, notifier=notifier))
    return RegisterResponse(result=result, feed=_page(session, feed_id, catalogue))
