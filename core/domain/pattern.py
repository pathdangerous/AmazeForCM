"""Recurring failure patterns and referred groups (phase 7.15, T1): the shapes core/steps/patterns.py returns."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

Dimension = Literal["weekday", "day_of_month", "provider_date"]
DIMENSIONS: tuple[Dimension, ...] = ("weekday", "day_of_month", "provider_date")


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    load_id: UUID
    received_on: date
    provider_date: date | None
    flagged_rows: int


class RulePattern(BaseModel):
    model_config = ConfigDict(frozen=True)

    feed_id: UUID
    feed_code: str
    rule_key: str
    dimension: Dimension
    value: str
    failing_loads: int
    loads_in_window: int  # the feed's loads (reruns left out) that had this value, failing or not
    evidence: list[Evidence]


class ReferredGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    feed_id: UUID
    feed_code: str
    rule_key: str
    pattern: str
    load_id: UUID
    reason: str
    decided_by: str
    referred_at: datetime
