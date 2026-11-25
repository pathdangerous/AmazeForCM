"""A feed's schedule (phase 7.21; migration 0039): recurring or a one-time load, and when a recurring feed is expected.

ScheduleInput is what a person enters; its validator says what the database checks too (weekly needs a weekday, monthly a
day, a one-time load has no frequency), so a refusal is named before anything is written. FeedSchedule is a stored version.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ScheduleKind = Literal["one_time", "recurring"]
Frequency = Literal["daily", "business_days", "weekly", "monthly"]
DayOfMonth = Annotated[str, StringConstraints(pattern=r"^([1-9]|1[0-9]|2[0-8]|last)$")]
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ScheduleKind
    frequency: Frequency | None = None
    weekday: Annotated[int, Field(ge=0, le=6)] | None = None  # 0 Monday .. 6 Sunday
    day_of_month: DayOfMonth | None = None  # '1'..'28' or 'last'
    expected_time: time | None = None
    grace_minutes: Annotated[int, Field(ge=0, le=1440)] = 0
    timezone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = "UTC"
    notify_owner: bool = True
    note: Annotated[str, StringConstraints(max_length=500)] | None = None

    @model_validator(mode="after")
    def _fits(self) -> ScheduleInput:
        if self.kind == "one_time":
            if any(v is not None for v in (self.frequency, self.weekday, self.day_of_month, self.expected_time)):
                raise ValueError("A one-time load has no frequency, weekday, day of month or expected time")
        else:
            if self.frequency is None or self.expected_time is None:
                raise ValueError("A recurring feed needs a frequency and an expected time")
            if (self.weekday is not None) != (self.frequency == "weekly"):
                raise ValueError("A weekly feed needs a weekday, and no other frequency has one")
            if (self.day_of_month is not None) != (self.frequency == "monthly"):
                raise ValueError("A monthly feed needs a day of the month, and no other frequency has one")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError(f"{self.timezone!r} is not a time zone name (for example Europe/London)") from exc
        return self

    def describe(self) -> str:
        """One line, as the pages write it."""
        if self.kind == "one_time":
            return "One-time load"
        when = {"daily": "every day", "business_days": "on business days",
                "weekly": f"every {WEEKDAYS[self.weekday or 0]}",
                "monthly": ("on the last day of the month" if self.day_of_month == "last"
                            else f"on day {self.day_of_month} of the month")}[self.frequency or "daily"]
        return f"Recurring, {when} at {self.expected_time:%H:%M} {self.timezone}, {self.grace_minutes} min grace"


class FeedSchedule(ScheduleInput):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    id: UUID
    feed_id: UUID
    version: int
    created_at: datetime
    decided_by: str

    def same_as(self, other: ScheduleInput) -> bool:
        return all(getattr(self, name) == getattr(other, name) for name in ScheduleInput.model_fields)
