"""Read-only operational facts for the backend overview; no sample or inferred figures."""
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.deps import backend_user, get_app_settings, get_db_session
from core.repositories.tables import (
    FeedRow, LoadCertificationRow, LoadRow, OnboardingSessionRow, ProposalLogEntry, ProposalRow,
)
from ports.config import Settings
from ports.identity import User

router = APIRouter(prefix="/api/amaze", tags=["overview"])


class RecentLoad(BaseModel):
    id: UUID
    feed_id: UUID
    feed_code: str
    feed_name: str
    source_code: str | None
    received_at: datetime
    status: str
    purpose: str
    rows: int | None
    flagged_rows: int | None
    trust_score: float | None


class PendingDecision(BaseModel):
    proposal_id: UUID
    feed_id: UUID
    feed_code: str
    feed_name: str
    session_id: UUID | None
    kind: str
    summary: str
    agent: str
    confidence: float | None


class FeedQuality(BaseModel):
    feed_id: UUID
    feed_code: str
    flagged_rows: int


class Quality(BaseModel):
    rows: int
    flagged_rows: int
    passing_rows: int
    pass_percent: float
    by_feed: list[FeedQuality]


class DriftEvent(BaseModel):
    """T4: a delivery that drifted from the last accepted structure (step 12's report, as stored on the load)."""

    load_id: UUID
    feed_id: UUID
    feed_code: str
    at: datetime
    outcome: str | None
    new_columns: int
    missing_columns: int
    type_changes: int
    order_changed: bool


class ActionCounts(BaseModel):
    """Profile and drift actions a person selected in the period, by who carries them out; and those still waiting."""

    by_agent: int
    by_hand: int
    pending: int


class Overview(BaseModel):
    days: int
    drift_events: list[DriftEvent] = []
    actions: ActionCounts = ActionCounts(by_agent=0, by_hand=0, pending=0)
    feeds_live: int
    feeds_paused: int
    onboardings_active: int
    loads_count: int
    average_trust_score: float | None
    release_threshold: float
    quality: Quality | None
    pending_decisions: list[PendingDecision]
    recent_loads: list[RecentLoad]


def _confidence(payload: dict) -> float | None:
    value = payload.get("confidence")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1 else None


@router.get("/overview", response_model=Overview)
def overview(
    user: Annotated[User, Depends(backend_user)],
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    days: int = 7,
) -> Overview:
    if days not in (1, 7, 30):
        raise HTTPException(status_code=422, detail="days must be 1, 7 or 30")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stages = dict(session.execute(select(FeedRow.stage, func.count()).group_by(FeedRow.stage)).all())
    active = session.scalar(select(func.count()).select_from(OnboardingSessionRow).where(
        OnboardingSessionRow.status == "active")) or 0
    records = session.execute(
        select(LoadRow, FeedRow, LoadCertificationRow)
        .join(FeedRow, FeedRow.id == LoadRow.feed_id)
        .outerjoin(LoadCertificationRow, LoadCertificationRow.load_id == LoadRow.id)
        .where(LoadRow.received_at >= since)
        .order_by(LoadRow.received_at.desc(), LoadRow.id)
    ).all()
    recent = [RecentLoad(
        id=load.id, feed_id=feed.id, feed_code=feed.code, feed_name=feed.name,
        source_code=feed.source_code, received_at=load.received_at, status=load.status,
        purpose=load.purpose, rows=cert.rows if cert else None,
        flagged_rows=cert.flagged_rows if cert else None,
        trust_score=float(cert.trust_score) if cert else None,
    ) for load, feed, cert in records]
    scores = [row.trust_score for row in recent if row.trust_score is not None]
    certified = [(feed, cert) for _, feed, cert in records if cert is not None]
    quality = None
    if certified:
        rows = sum(cert.rows for _, cert in certified)
        flagged = sum(cert.flagged_rows for _, cert in certified)
        by_feed: dict[UUID, FeedQuality] = {}
        for feed, cert in certified:
            item = by_feed.setdefault(feed.id, FeedQuality(feed_id=feed.id, feed_code=feed.code, flagged_rows=0))
            item.flagged_rows += cert.flagged_rows
        if rows:
            quality = Quality(
                rows=rows, flagged_rows=flagged, passing_rows=rows - flagged,
                pass_percent=100 * (rows - flagged) / rows,
                by_feed=sorted(by_feed.values(), key=lambda item: (-item.flagged_rows, item.feed_code)),
            )
    proposals = session.execute(
        select(ProposalRow, FeedRow, ProposalLogEntry.agent_name, OnboardingSessionRow.id)
        .join(FeedRow, FeedRow.id == ProposalRow.feed_id)
        .outerjoin(ProposalLogEntry, ProposalLogEntry.id == ProposalRow.proposal_log_id)  # a manual or rule-made item has none
        .outerjoin(OnboardingSessionRow, OnboardingSessionRow.feed_id == FeedRow.id)
        .where(ProposalRow.status == "pending",
               ProposalRow.required_approver_roles.op("&&")(user.roles))
        .order_by(ProposalRow.created_at, ProposalRow.id)
    ).all()
    pending = []
    for proposal, feed, agent, session_id in proposals:
        payload = proposal.payload
        agent = agent or ("rules" if proposal.origin == "rule" else f"added by {proposal.author}")
        summary = next((payload[key] for key in ("summary", "reason", "source_field", "column")
                        if isinstance(payload.get(key), str) and payload[key]), proposal.kind.replace("_", " "))
        pending.append(PendingDecision(
            proposal_id=proposal.id, feed_id=feed.id, feed_code=feed.code, feed_name=feed.name,
            session_id=session_id, kind=proposal.kind, summary=summary, agent=agent,
            confidence=_confidence(payload),
        ))
    drift_events = []
    for load, feed, _ in records:
        found = (load.arrival_detail or {}).get("drift")
        if found and load.purpose == "scheduled":
            drift_events.append(DriftEvent(
                load_id=load.id, feed_id=feed.id, feed_code=feed.code, at=load.received_at, outcome=load.arrival_outcome,
                new_columns=len(found.get("new_columns", [])), missing_columns=len(found.get("missing_columns", [])),
                type_changes=len(found.get("type_changes", [])), order_changed=bool(found.get("order_changed"))))
    actions = session.execute(select(ProposalRow.status, ProposalRow.payload, ProposalRow.decided_at)
                              .where(ProposalRow.kind == "profile_action")).all()
    selected = [payload for status, payload, decided in actions if status == "approved" and decided is not None and decided >= since]
    counts = ActionCounts(by_agent=sum(p.get("applied_by") == "agent" for p in selected),
                          by_hand=sum(p.get("applied_by") == "manual" for p in selected),
                          pending=sum(status == "pending" for status, _, _ in actions))
    return Overview(
        drift_events=drift_events, actions=counts,
        days=days, feeds_live=stages.get("registered", 0), feeds_paused=stages.get("paused", 0),
        onboardings_active=active, loads_count=len(recent),
        average_trust_score=sum(scores) / len(scores) if scores else None,
        release_threshold=settings.trust_release_threshold, quality=quality,
        pending_decisions=pending, recent_loads=recent,
    )
