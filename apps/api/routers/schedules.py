"""Phase 7.21: a feed's schedule (plan docs/plans/phase-7.21.md, assumption 14). No model call.

GET  /api/amaze/feeds/{feed_id}/schedule   any backend role: the version in force and every version, oldest first.
POST /api/amaze/feeds/{feed_id}/schedule   the backend team: a new version (or schedule_unchanged when nothing differs).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.deps import backend_team_user, backend_user, get_db_session
from apps.api.transactions import in_transaction, read
from core.domain.schedule import FeedSchedule, ScheduleInput
from core.repositories.feeds import FeedRepository
from core.repositories.schedules import FeedScheduleRepository
from core.steps.deliveries import (DeliveriesOverview, DeliveriesResult, Window, deliveries_for_feed,
                                   deliveries_overview)
from core.steps.schedule import ScheduleResult, set_schedule
from ports.identity.base import User

router = APIRouter(prefix="/api/amaze", tags=["schedules"], dependencies=[Depends(backend_user)])
DbSession = Annotated[Session, Depends(get_db_session)]


class ScheduleView(BaseModel):
    current: FeedSchedule | None  # None: "Schedule not set"
    versions: list[FeedSchedule]


@router.get("/feeds/{feed_id}/schedule", response_model=ScheduleView)
def get_schedule(feed_id: UUID, session: DbSession) -> ScheduleView:
    def work() -> ScheduleView:
        FeedRepository(session).get(feed_id)  # FeedNotFound: 404
        versions = FeedScheduleRepository(session).list_for_feed(feed_id)
        return ScheduleView(current=versions[-1] if versions else None, versions=versions)

    return read(session, work)


@router.get("/feeds/{feed_id}/deliveries", response_model=DeliveriesResult)
def get_deliveries(feed_id: UUID, session: DbSession, window: Window = "30"):
    """Expected against actual deliveries (typed: deliveries_shown, one_time_shown or schedule_not_set). Read only."""
    return read(session, lambda: deliveries_for_feed(session, feed_id, window=window))


@router.get("/deliveries/overview", response_model=DeliveriesOverview)
def get_overview(session: DbSession):
    """The recurring feeds by on-time share over 30 days, worst first. Read only."""
    return read(session, lambda: deliveries_overview(session))


@router.post("/feeds/{feed_id}/schedule", response_model=ScheduleResult)
def change_schedule(feed_id: UUID, body: ScheduleInput, session: DbSession,
                    user: Annotated[User, Depends(backend_team_user)]):
    return in_transaction(session, lambda: set_schedule(session, feed_id=feed_id, schedule=body, user=user))
