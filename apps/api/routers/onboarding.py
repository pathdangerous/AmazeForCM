"""The onboarding workspace (phase 7.8, stage S5, task 5.4; plan assumptions 31 to 39). One operator onboards an uploaded
file step by step; the steps are core/steps/onboarding.py over the existing step functions.

GET  /api/amaze/onboardings                       every session: its feed, current step, step states, status, calls
GET  /api/amaze/onboardings/{id}[?step=<step>]    the workspace view (steps, the viewed step's items, the job, notes,
                                                  inputs); another step than the current is read only
POST /api/amaze/onboardings/{id}/steps/{step}/enter    run the step's agent in the background (queues its job)
POST /api/amaze/onboardings/{id}/approve          Approve & next; 409 decision_needed lists the items to decide first
POST /api/amaze/onboardings/{id}/steps/{step}/approve-confident   one approval per confident item of the current step,
                                                  by the caller (the step does not move on)
POST /api/amaze/onboardings/{id}/back             {to_step}: later steps become stale and the step reruns
POST /api/amaze/onboardings/{id}/items/{proposal_id}/decision   one item, by the decision code of
                                                  POST /api/amaze/proposals/{id}/decision (decide_proposal)
POST /api/amaze/onboardings/{id}/items/{proposal_id}/edit       {payload}: a new version, "edited by" (a rule is back-tested again)
POST /api/amaze/onboardings/{id}/items                          a manual item (T7): {kind: glossary_element | mapping |
                                                  transformation | rule | review_summary, ...}; 409 pending, 422 invalid
PUT  /api/amaze/onboardings/{id}/key-policy       the feed's duplicate-key policy (step 10)
GET  /api/amaze/onboardings/{id}/timeline         the audit trail: events, proposals with their calls, edits, approvals

Every route needs a backend role (403 otherwise; 401 with no user); the steps check the specific role. Each committing
route is one transaction (apps/api/transactions.py); the worker is kicked after a commit that queued a job.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from apps.api.deps import (
    backend_user,
    get_app_settings,
    get_db_session,
    get_identity_port,
    get_onboarding_context,
    get_onboarding_kick,
    get_request_notifier,
)
from apps.api.routers.amaze import DecisionBody, decide_proposal
from apps.api.transactions import in_transaction, read
from core.domain.common import NonBlank
from core.domain.manual import ManualItem, ManualRule
from core.domain.onboarding import (
    ApproveOptions,
    ConfidentApproved,
    StepAdvance,
    StepName,
    TimelineEntry,
    Workspace,
)
from core.domain.proposal import Proposal
from core.domain.transformation import KeyColumns, KeyPolicy, KeyPolicyName
from core.repositories.errors import DecisionNeeded, ManualRuleRefused, ProposalNotFound
from core.repositories.feeds import FeedRepository
from core.repositories.onboarding import OnboardingRepository
from core.repositories.proposals import ProposalRepository
from core.steps.onboarding import (
    OnboardingContext,
    add_item,
    approve_and_next,
    back,
    checked_rule_payload,
    edit_item,
    enter_step,
    approve_confident,
    manual_rule_input,
    timeline,
    workspace,
)
from core.steps.s10_transform import set_key_policy
from ports.config import Settings
from ports.identity import Identity, User
from ports.notify import Notifier

router = APIRouter(prefix="/api/amaze/onboardings", tags=["onboarding"], dependencies=[Depends(backend_user)])

DbSession = Annotated[Session, Depends(get_db_session)]
Operator = Annotated[User, Depends(backend_user)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
Context = Annotated[OnboardingContext, Depends(get_onboarding_context)]
Kick = Annotated[Any, Depends(get_onboarding_kick)]
IdentityPort = Annotated[Identity, Depends(get_identity_port)]
RequestNotifier = Annotated[Notifier, Depends(get_request_notifier)]


class OnboardingRow(BaseModel):
    id: UUID
    feed_id: UUID
    feed_code: str
    feed_name: str
    current_step: StepName
    step_states: dict[str, str]
    status: str
    created_by: str
    created_at: Any
    updated_at: Any
    model_calls: int
    model_call_cap: int


class BackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_step: StepName


class EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]


class KeyPolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: KeyPolicyName
    key_columns: KeyColumns
    reason: NonBlank


class Advanced(BaseModel):
    advance: StepAdvance
    workspace: Workspace


class ItemDecided(BaseModel):
    result: Any  # the kind's own typed result (mapped, element decided, rule stored ...)
    workspace: Workspace


class ItemEdited(BaseModel):
    proposal: Proposal
    workspace: Workspace


class ItemAdded(BaseModel):
    proposal: Proposal
    workspace: Workspace


class KeyPolicyStored(BaseModel):
    key_policy: KeyPolicy
    workspace: Workspace


def _view(session: Session, session_id: UUID, settings: Settings, step: StepName | None = None) -> Workspace:
    return read(session, lambda: workspace(session, session_id=session_id, settings=settings, step=step))


@router.get("", response_model=list[OnboardingRow])
def onboardings(session: DbSession, settings: AppSettings) -> list[OnboardingRow]:
    def rows():
        feeds = FeedRepository(session)
        found = []
        for s in OnboardingRepository(session).list():
            feed = feeds.get(s.feed_id)
            found.append(OnboardingRow(id=s.id, feed_id=s.feed_id, feed_code=feed.code, feed_name=feed.name,
                                       current_step=s.current_step, step_states=s.step_states, status=s.status,
                                       created_by=s.created_by, created_at=s.created_at, updated_at=s.updated_at,
                                       model_calls=s.model_calls, model_call_cap=settings.max_model_calls_per_onboarding))
        return found

    return read(session, rows)


@router.get("/{session_id}", response_model=Workspace,
            responses={422: {"description": "the step is not one of the nine steps"}})
def get_workspace(session_id: UUID, session: DbSession, settings: AppSettings,
                  step: StepName | None = None) -> Workspace:
    """?step=<step> views any step read only (its items, notes and inputs); the default is the current step."""
    return _view(session, session_id, settings, step)


@router.post("/{session_id}/steps/{step}/enter", response_model=Workspace)
def enter(session_id: UUID, step: StepName, session: DbSession, user: Operator, settings: AppSettings,
          kick: Kick) -> Workspace:
    in_transaction(session, lambda: enter_step(session, session_id=session_id, step=step, user=user))
    kick()  # after the commit: the worker sees the job
    return _view(session, session_id, settings)


@router.post("/{session_id}/approve", response_model=Advanced,
             responses={409: {"description": "decision_needed (with the items), or the step cannot be approved"}})
def approve(session_id: UUID, options: ApproveOptions, session: DbSession, user: Operator, ctx: Context,
            settings: AppSettings, kick: Kick) -> Advanced:
    try:
        advance = in_transaction(session, lambda: approve_and_next(session, session_id=session_id, user=user, ctx=ctx,
                                                                   options=options))
    except DecisionNeeded as exc:  # rolled back by in_transaction; the body says which items
        raise HTTPException(status_code=409, detail={
            "outcome": "decision_needed", "message": str(exc),
            "items": [i.model_dump(mode="json") if hasattr(i, "model_dump") else i for i in exc.items]}) from None
    kick()
    return Advanced(advance=advance, workspace=_view(session, session_id, settings))


@router.post("/{session_id}/steps/{step}/approve-confident", response_model=ConfidentApproved,
             responses={409: {"description": "not the current step, not an item step, or its agent has not answered"}})
def approve_all_confident(session_id: UUID, step: StepName, session: DbSession, user: Operator,
                          ctx: Context) -> ConfidentApproved:
    """Approve all confident of the current step: the person's own decision on each item, one approval row each."""
    return in_transaction(session, lambda: approve_confident(session, session_id=session_id, step=step,
                                                             user=user, ctx=ctx))


@router.post("/{session_id}/back", response_model=Workspace)
def go_back(session_id: UUID, body: BackBody, session: DbSession, user: Operator, settings: AppSettings,
            kick: Kick) -> Workspace:
    in_transaction(session, lambda: back(session, session_id=session_id, to_step=body.to_step, user=user))
    kick()
    return _view(session, session_id, settings)


@router.post("/{session_id}/items/{proposal_id}/decision", response_model=ItemDecided)
def decide_item(session_id: UUID, proposal_id: UUID, body: DecisionBody, session: DbSession, user: Operator,
                identity: IdentityPort, notifier: RequestNotifier, settings: AppSettings) -> ItemDecided:
    def work():
        sessions = OnboardingRepository(session)
        sess = sessions.lock(session_id)  # OnboardingNotFound: 404
        if ProposalRepository(session).get(proposal_id).feed_id != sess.feed_id:
            raise ProposalNotFound(f"Proposal {proposal_id} is not an item of onboarding {session_id}")
        result = decide_proposal(session, proposal_id=proposal_id, body=body, user=user, identity=identity,
                                 notifier=notifier, settings=settings)
        sessions.add_event(session_id, step=sess.current_step, event=body.decision, actor=user.id,
                           proposal_id=proposal_id, detail={"note": body.note} if body.note else {})
        return result

    result = in_transaction(session, work)
    return ItemDecided(result=result, workspace=_view(session, session_id, settings))


@router.post("/{session_id}/items", response_model=ItemAdded,
             responses={409: {"description": "the column or rule key has a pending item, or the step is not ready"},
                        422: {"description": "the item is not valid (a rule that fails its back-test says why)"}})
def add(session_id: UUID, body: ManualItem, session: DbSession, user: Operator, ctx: Context,
        settings: AppSettings) -> ItemAdded:
    """A person adds an item to the current step (phase 7.12, T7): a manual proposal, no model call. A rule is read, then
    back-tested with no connection held (step 09's code), then written."""
    rule_payload = None
    if isinstance(body, ManualRule):
        agent_input, proposed, own = read(
            session, lambda: manual_rule_input(session, session_id=session_id, item=body, ctx=ctx))
        rule_payload = _back_tested(agent_input, proposed, own, ctx)
    proposal = in_transaction(session, lambda: add_item(session, session_id=session_id, item=body, user=user,
                                                        rule_payload=rule_payload))
    return ItemAdded(proposal=proposal, workspace=_view(session, session_id, settings))


def _back_tested(agent_input, proposed, own, ctx) -> dict[str, Any]:
    try:
        return checked_rule_payload(agent_input, proposed, own, ctx=ctx)
    except ManualRuleRefused as exc:  # named, never a 500: the person sees why the SQL was refused
        raise HTTPException(status_code=422, detail=exc.reason) from None


@router.post("/{session_id}/items/{proposal_id}/edit", response_model=ItemEdited,
             responses={409: {"description": "the item is no longer pending"},
                        422: {"description": "the edited payload is not valid for its kind"}})
def edit(session_id: UUID, proposal_id: UUID, body: EditBody, session: DbSession, user: Operator, ctx: Context,
         settings: AppSettings) -> ItemEdited:
    payload = body.payload
    kind = read(session, lambda: ProposalRepository(session).get(proposal_id).kind)
    if kind == "rule":  # an edited rule is back-tested again, like a new one (T7)
        agent_input, proposed, own = read(session, lambda: manual_rule_input(
            session, session_id=session_id, item=None, ctx=ctx, edit_of=proposal_id, edited=payload))
        payload = _back_tested(agent_input, proposed, own, ctx)
    proposal = in_transaction(session, lambda: edit_item(session, session_id=session_id, proposal_id=proposal_id,
                                                         payload=payload, user=user))
    return ItemEdited(proposal=proposal, workspace=_view(session, session_id, settings))


@router.put("/{session_id}/key-policy", response_model=KeyPolicyStored)
def key_policy(session_id: UUID, body: KeyPolicyBody, session: DbSession, user: Operator,
               settings: AppSettings) -> KeyPolicyStored:
    def work():
        sess = OnboardingRepository(session).get(session_id)  # OnboardingNotFound: 404
        return set_key_policy(session, feed_id=sess.feed_id, user=user, policy=body.policy,
                              key_columns=list(body.key_columns), reason=body.reason)

    stored = in_transaction(session, work)
    return KeyPolicyStored(key_policy=stored.key_policy, workspace=_view(session, session_id, settings))


@router.get("/{session_id}/timeline", response_model=list[TimelineEntry])
def audit_timeline(session_id: UUID, session: DbSession) -> list[TimelineEntry]:
    return read(session, lambda: timeline(session, session_id=session_id))
