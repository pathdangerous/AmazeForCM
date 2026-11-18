"""Amaze platform API (backend team). Phase 1: read the glossary and the feed registry. Phase 4: steps 02 to
04 on one feed (plan, section 9): the feed page and the data owners for any backend role; profile,
summary, sign-off and register for the backend team only. Phase 5: steps 05 and 06 (plan, section 9): the
runs banner, a glossary run, the proposals, Propose mappings and the decisions. Each commit that queued a run
is followed by a kick of the worker.

Each step is one transaction (apps/api/transactions.py). Step 02 reads, ends the transaction, runs DuckDB
with no connection held, then writes; the summary reads, ends the transaction, calls the model, then writes.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints
from sqlalchemy.orm import Session

from agents.mapping import MappingAgent
from agents.profiler import ProfilerAgent
from agents.review_summariser import ReviewSummariserAgent
from agents.rule_recommender import RuleRecommenderAgent
from apps.api.deps import (
    backend_team_user,
    backend_user,
    data_steward_user,
    get_app_settings,
    get_blob_store_port,
    get_connector_factory,
    get_daily_fetch,
    get_db_session,
    get_glossary_kick,
    get_identity_port,
    get_mapping_agent,
    get_profiler_agent,
    get_request_notifier,
    get_review_summariser,
    get_rule_agent,
    get_source_catalogue,
    glossary_run_user,
    operations_user,
)
from apps.api.jobs import DAILY_FETCH_HOUR, DailyFetch
from apps.api.transactions import in_transaction, read
from core.domain.feed import Feed, FeedStage
from core.domain.glossary import GlossaryElement
from core.domain.mapping import GlossaryRun
from core.domain.proposal import ProposalStatus
from core.domain.load import StandardRecord
from core.engine.run import execute_load, plan_load, record_load
from core.repositories.errors import OwnerNotADataOwner, ProposalNotFound, UnknownGlossaryElement
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.standard_records import StandardRecordRepository
from core.steps.errors import ChoiceNotOffered
from core.steps.feed_page import (
    FeedPage,
    LoadOnPage,
    ProposalOnPage,
    configuration_proposals,
    feed_page,
    load_on_page,
    proposal_on_page,
)
from core.steps.s09_rules import (
    RuleAwaitsChecker,
    RuleRejected,
    RulesResult,
    RuleStored,
    backtest_for,
    decide_rule,
    record_rules,
    rules_input,
)
from core.steps.s11_go_live import (
    ApprovedScheduled,
    GoLiveSummary,
    RejectedBackToStep,
    TestFailedHeld,
    after_test_load,
    begin_test_load,
    decide_go_live,
    go_live_plan,
)
from core.steps.s11_go_live import record_summary as record_go_live_summary  # s02 has a record_summary too
from core.steps.s02_profile import ProfileResult, ProfileSummary, profile_plan, record_profile, record_summary, \
    run_profile, summary_input
from core.steps.s03_signoff import SignoffResult, sign_off
from core.steps.s04_register import NoOwner, Registered, register_feed
from core.steps.s05_glossary import ElementDecided, RunsBanner, decide_element, queue_run, runs_banner
from core.steps.s06_map import Mapped, MappingRejected, MappingsProposed, decide_mapping, mapping_input, \
    record_mappings
from ports.config import Settings
from ports.connectors import SourceConnector
from ports.connectors.catalogue import SourceCatalogue, UnknownSource
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
Mapper = Annotated[MappingAgent, Depends(get_mapping_agent)]
Kick = Annotated[Callable[[], None], Depends(get_glossary_kick)]
GlossaryRunner = Annotated[User, Depends(glossary_run_user)]
Steward = Annotated[User, Depends(data_steward_user)]
Operations = Annotated[User, Depends(operations_user)]
RuleAgent = Annotated[RuleRecommenderAgent, Depends(get_rule_agent)]
Summariser = Annotated[ReviewSummariserAgent, Depends(get_review_summariser)]
Connectors = Annotated[Callable[[str], SourceConnector], Depends(get_connector_factory)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
Daily = Annotated[DailyFetch, Depends(get_daily_fetch)]
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


class FeedRow(Feed):
    """A registry row (phase 7.5, P4): the feed and its catalogue entry's name (None when the feed has no catalogue
    source or the catalogue no longer lists it)."""

    source_name: str | None


def _source_name(catalogue: SourceCatalogue, feed: Feed) -> str | None:
    try:
        return catalogue.get(feed.source_code).name if feed.source_code else None
    except UnknownSource:
        return None


@router.get("/feeds", response_model=list[FeedRow])
def feeds(session: DbSession, catalogue: Catalogue, stage: FeedStage | None = None) -> list[FeedRow]:
    return [FeedRow(**feed.model_dump(), source_name=_source_name(catalogue, feed))
            for feed in FeedRepository(session).list(stage=stage)]


class Person(BaseModel):
    id: str
    display_name: str


@router.get("/people", response_model=list[Person])
def people(identity: IdentityPort) -> list[Person]:
    """Phase 7.5, P3 and P8 (plan, assumption 12): every user's display name, for the ids the pages show."""
    return [Person(id=u.id, display_name=u.display_name) for u in identity.list_users()]


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
             identity: IdentityPort, notifier: RequestNotifier, kick: Kick) -> RegisterResponse:
    result = in_transaction(session, lambda: register_feed(
        session, feed_id=feed_id, user=user, owner_id=body.owner_id, visibility=body.visibility,
        catalogue=catalogue, identity=identity, notifier=notifier))
    if isinstance(result, Registered):  # phase 5: the run it queued is committed; wake the worker
        kick()
    return RegisterResponse(result=result, feed=_page(session, feed_id, catalogue))


# ---- phase 5: steps 05 and 06 (plan, section 9)


class GlossaryRunResponse(BaseModel):
    run: GlossaryRun
    created: bool  # False: the feed's active run is returned


class ProposeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None  # None: every unmapped one


class ProposeResponse(BaseModel):
    result: MappingsProposed
    feed: FeedPage


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    # a glossary proposal's option index, or a mapping proposal's element key; None: the recommended one
    choice: StrictInt | Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    note: Text | None = None
    back_to_step: Literal["05", "06", "09"] | None = None  # a go-live rejection names the step it goes back to


class DecisionResponse(BaseModel):
    result: (Mapped | MappingRejected | ElementDecided | RuleAwaitsChecker | RuleStored | RuleRejected
             | ApprovedScheduled | RejectedBackToStep)
    proposal: ProposalOnPage


def _users_with(identity: Identity, role: str) -> list[str]:
    return [u.id for u in identity.list_users() if role in u.roles]


def _description(catalogue: SourceCatalogue, feed: Feed) -> str | None:
    try:
        return catalogue.get(feed.source_code).description if feed.source_code else None
    except UnknownSource:
        return None


@router.get("/glossary/runs", response_model=RunsBanner)
def glossary_runs(session: DbSession) -> RunsBanner:
    return read(session, lambda: runs_banner(session))


@router.post("/feeds/{feed_id}/glossary/run", response_model=GlossaryRunResponse)
def glossary_run(feed_id: UUID, session: DbSession, user: GlossaryRunner, kick: Kick) -> GlossaryRunResponse:
    run, created = in_transaction(session, lambda: queue_run(session, feed_id=feed_id, reason="requested",
                                                             requested_by=user.id))
    kick()  # after the commit: the worker sees the run
    return GlossaryRunResponse(run=run, created=created)


@router.get("/proposals", response_model=list[ProposalOnPage])
def proposals(session: DbSession, kind: Literal["glossary_element", "mapping", "rule", "review_summary"] | None = None,
              status: ProposalStatus | None = None, feed_id: UUID | None = None) -> list[ProposalOnPage]:
    return read(session, lambda: configuration_proposals(session, kind=kind, status=status, feed_id=feed_id))


@router.post("/feeds/{feed_id}/mappings/propose", response_model=ProposeResponse)
def propose_mappings(feed_id: UUID, body: ProposeBody, session: DbSession, user: Steward, catalogue: Catalogue,
                     agent: Mapper, kick: Kick) -> ProposeResponse:
    def build():
        feed = FeedRepository(session).get(feed_id)
        return mapping_input(session, feed_id=feed_id, column=body.column,
                             source_description=_description(catalogue, feed))

    agent_input = read(session, build)
    result = in_transaction(session, lambda: record_mappings(session, agent=agent, agent_input=agent_input, user=user))
    if any(getattr(r, "glossary_run_id", None) for r in result.results):  # a sent-back run was queued
        kick()
    return ProposeResponse(result=result, feed=_page(session, feed_id, catalogue))


@router.post("/proposals/{proposal_id}/decision", response_model=DecisionResponse)
def decide(proposal_id: UUID, body: DecisionBody, session: DbSession, user: Annotated[User, Depends(backend_user)],
           identity: IdentityPort, notifier: RequestNotifier, settings: AppSettings) -> DecisionResponse:
    def work():
        kind = ProposalRepository(session).get(proposal_id).kind
        if kind == "glossary_element":
            if isinstance(body.choice, str):
                raise ChoiceNotOffered("A glossary proposal's choice is the index of one of its options")
            return decide_element(session, proposal_id=proposal_id, user=user, decision=body.decision,
                                  choice=body.choice, note=body.note, notifier=notifier,
                                  stewards=_users_with(identity, "data_steward"))
        if kind == "mapping":
            if isinstance(body.choice, int):
                raise ChoiceNotOffered("A mapping proposal's choice is one of its candidate element keys")
            return decide_mapping(session, proposal_id=proposal_id, user=user, decision=body.decision,
                                  choice=body.choice, note=body.note)
        if kind == "rule":  # phase 6: the steward, then the checker
            return decide_rule(session, proposal_id=proposal_id, user=user, decision=body.decision, note=body.note,
                               notifier=notifier, checkers=_users_with(identity, "checker"))
        if kind == "review_summary":  # phase 6: the feed's owner
            return decide_go_live(
                session, proposal_id=proposal_id, user=user, decision=body.decision, note=body.note,
                back_to_step=body.back_to_step, today=date.today(), hypercare_days=settings.hypercare_days,
                daily_run_utc=f"{DAILY_FETCH_HOUR:02d}:00", notifier=notifier, backend_team=_backend_team(identity),
                operations=_users_with(identity, "operations"), role_holders=lambda role: _users_with(identity, role))
        raise ProposalNotFound(f"No glossary, mapping, rule or go-live proposal with id {proposal_id}")

    result = in_transaction(session, work)
    return DecisionResponse(result=result, proposal=read(session, lambda: proposal_on_page(session, proposal_id)))


# ---- phase 6: steps 09, 11 and 13 (plan, section 9)


class ProposeRulesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposeRulesResponse(BaseModel):
    result: RulesResult
    feed: FeedPage


class GoLiveResponse(BaseModel):
    result: GoLiveSummary | TestFailedHeld
    proposal: ProposalOnPage | None  # the summary waiting for the owner; None when the test load failed
    feed: FeedPage


class LoadNowResponse(BaseModel):
    result: dict[str, str | None]  # {"fetch": the fetch's outcome, "load": the load's status, or None}
    feed: FeedPage


class LoadDetail(BaseModel):
    load: LoadOnPage
    records: list[StandardRecord]  # the first LOAD_RECORDS by row number


LOAD_RECORDS = 50


@router.post("/feeds/{feed_id}/rules/propose", response_model=ProposeRulesResponse)
def propose_rules(feed_id: UUID, body: ProposeRulesBody, session: DbSession, user: Steward, catalogue: Catalogue,
                  agent: RuleAgent, connectors: Connectors, settings: AppSettings,
                  blobs: Blobs) -> ProposeRulesResponse:
    agent_input = read(session, lambda: rules_input(session, feed_id=feed_id, catalogue=catalogue))
    backtest = backtest_for(agent_input, connector_factory=connectors, blob_store=blobs,
                            rule_timeout_seconds=settings.rule_timeout_seconds)  # runs after the call, no connection
    result = in_transaction(session, lambda: record_rules(session, agent=agent, agent_input=agent_input,
                                                           backtest=backtest))
    return ProposeRulesResponse(result=result, feed=_page(session, feed_id, catalogue))


@router.post("/feeds/{feed_id}/go-live/summary", response_model=GoLiveResponse)
def go_live_summary(feed_id: UUID, session: DbSession, user: BackendTeam, catalogue: Catalogue, blobs: Blobs,
                    agent: Summariser, notifier: RequestNotifier, identity: IdentityPort,
                    settings: AppSettings) -> GoLiveResponse:
    """Step 11 prepare. Each part its own transaction, and no connection is held while the file is read, a rule
    runs or the model answers: the checks; a received test load (committed, so no second one starts); the
    engine's plan, execution and locked write; the held notice or the summary's input; the summary."""
    team = _backend_team(identity)

    def owner_is_data_owner(owner_id: str) -> bool:
        owner = identity.get_user(owner_id)
        return owner is not None and "data_owner" in owner.roles

    def checks():
        try:
            return go_live_plan(session, feed_id=feed_id, owner_is_data_owner=owner_is_data_owner,
                                notifier=notifier, backend_team=team)
        except OwnerNotADataOwner:
            session.commit()  # the note to the backend team is kept; the answer is still 409
            raise

    plan = in_transaction(session, checks)
    load = in_transaction(session, lambda: begin_test_load(
        session, plan, stale_minutes=settings.load_stale_minutes, notifier=notifier,
        operations=_users_with(identity, "operations")))
    engine_plan = read(session, lambda: plan_load(session, load.id))
    outcome = execute_load(engine_plan, blob_store=blobs, rule_timeout_seconds=settings.rule_timeout_seconds)
    load_run = in_transaction(session, lambda: record_load(session, engine_plan, outcome))
    after = in_transaction(session, lambda: after_test_load(session, plan=plan, load_run=load_run,
                                                            notifier=notifier, backend_team=team))
    if isinstance(after, TestFailedHeld):
        return GoLiveResponse(result=after, proposal=None, feed=_page(session, feed_id, catalogue))
    summary = in_transaction(session, lambda: record_go_live_summary(session, agent=agent, agent_input=after))
    return GoLiveResponse(result=summary, feed=_page(session, feed_id, catalogue),
                          proposal=read(session, lambda: proposal_on_page(session, summary.proposal_id)))


@router.post("/feeds/{feed_id}/loads", response_model=LoadNowResponse)
def load_now(feed_id: UUID, session: DbSession, user: Operations, catalogue: Catalogue,
             daily: Daily) -> LoadNowResponse:
    """Operations' Load now: the job's own fetch and load for this one feed (409 NotLive without a go-live)."""
    result = in_transaction(session, lambda: daily.fetch_and_load(feed_id), keep=False)
    return LoadNowResponse(result=result, feed=_page(session, feed_id, catalogue))


@router.get("/loads/{load_id}", response_model=LoadDetail)
def load_detail(load_id: UUID, session: DbSession) -> LoadDetail:
    def work():
        load = LoadRepository(session).get(load_id)
        return LoadDetail(load=load_on_page(session, load),
                          records=StandardRecordRepository(session).list_for_load(load_id, limit=LOAD_RECORDS))

    return read(session, work)
