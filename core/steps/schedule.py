"""Setting and changing a feed's schedule (phase 7.21, T1; plan docs/plans/phase-7.21.md).

A schedule is a person's setting, not an agent's proposal: there is no model call. A change is a new version (rule 7); the
person and the moment are kept. The feed row is locked first, so two changes of one feed are made one after the other.
Outcomes: schedule_set (a new version), schedule_unchanged (the same as the version in force: nothing is written).
The step flushes; the caller commits.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy.orm import Session

from core.domain.schedule import FeedSchedule, ScheduleInput
from core.repositories.feeds import FeedRepository
from core.repositories.schedules import FeedScheduleRepository
from core.steps.result import StepResult
from ports.identity.base import User


class ScheduleSet(StepResult):
    outcome: Literal["schedule_set"] = "schedule_set"
    schedule: FeedSchedule


class ScheduleUnchanged(StepResult):
    outcome: Literal["schedule_unchanged"] = "schedule_unchanged"
    schedule: FeedSchedule


ScheduleResult = Annotated[ScheduleSet | ScheduleUnchanged, Field(discriminator="outcome")]


def set_schedule(session: Session, *, feed_id: UUID, schedule: ScheduleInput, user: User) -> ScheduleSet | ScheduleUnchanged:
    """FeedNotFound for an unknown feed. The caller's role is checked by the API (the backend team)."""
    FeedRepository(session).lock(feed_id)
    schedules = FeedScheduleRepository(session)
    current = schedules.current(feed_id)
    if current is not None and current.same_as(schedule):
        return ScheduleUnchanged(schedule=current)
    return ScheduleSet(schedule=schedules.add_version(feed_id=feed_id, schedule=schedule, decided_by=user.id))
