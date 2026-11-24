"""Reports (phase 7.15; plan docs/plans/phase-7.15.md, 2.2).

ReportNumbers is everything a report says in figures, counted by code from the tables: no model writes or changes one.
A Report is its stored, append-only, versioned row: the numbers (and the hash the database made of them), and a narrative
that is separate, optional and logged. The ReportSummariserAgent (A1, read-only) sees the numbers and answers a few
sentences; a sentence that states a figure the numbers do not hold is refused (core/steps/reports.py).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from core.domain.common import NonBlank
from core.domain.pattern import ReferredGroup, RulePattern

ReportKind = Literal["daily", "monthly"]
SubjectRole = Literal["data_owner", "data_steward"]
NarrativeStatus = Literal["written", "refused", "none"]


class Period(BaseModel):
    """One UTC day or calendar month: start inclusive, end exclusive."""

    model_config = ConfigDict(frozen=True)

    kind: ReportKind
    start: date
    end: date

    @classmethod
    def daily(cls, day: date) -> Period:
        return cls(kind="daily", start=day, end=day + timedelta(days=1))

    @classmethod
    def monthly(cls, year: int, month: int) -> Period:
        first = date(year, month, 1)
        return cls(kind="monthly", start=first, end=date(year + (month == 12), month % 12 + 1, 1))


class PeriodNumbers(BaseModel):
    kind: ReportKind
    start: str  # ISO dates, so a narrative that names them is checked against these
    end: str


class Totals(BaseModel):
    feeds: int
    loads: int
    released: int
    held: int
    failed: int
    quarantined: int
    rows: int
    flagged_rows: int
    waived_rows: int
    average_trust_score: Decimal | None  # over the certified loads of the period; None when there are none


class FeedNumbers(BaseModel):
    feed_id: UUID
    feed_code: str
    loads: int
    released: int
    held: int
    failed: int
    quarantined: int
    rows: int
    flagged_rows: int
    waived_rows: int
    average_trust_score: Decimal | None
    open_now: int  # exception groups of its held loads with no approved fix, as of the report
    waiting_for_owner: int
    resolved_by_agent: int  # fixes approved in the period, by the origin of the approved proposal
    resolved_by_hand: int
    waivers: int
    referred_groups: int
    reruns_released: int
    reruns_still_held: int
    drift_events: int
    patterns: int


class ExceptionNumbers(BaseModel):
    open_now: int
    waiting_for_owner: int
    resolved_by_agent: int
    resolved_by_hand: int


class RerunNumbers(BaseModel):
    released: int
    still_held: int


class DecisionNumbers(BaseModel):
    approved: int
    rejected: int
    by_kind: dict[str, int]


class ReportNumbers(BaseModel):
    subject_id: str
    subject_role: SubjectRole
    period: PeriodNumbers
    totals: Totals
    feeds: list[FeedNumbers]
    exceptions: ExceptionNumbers
    waivers: int
    reruns: RerunNumbers
    drift_events: int
    patterns: list[RulePattern]
    referred_groups: list[ReferredGroup]  # each once (plan, assumption 5)
    decisions: DecisionNumbers | None  # a data steward's own decisions in the period; None for an owner


class ReportSummaryInput(BaseModel):
    """What the ReportSummariserAgent sees: the report's numbers (a pattern's evidence dates left out: its counts stay)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_role: SubjectRole
    kind: ReportKind
    numbers: dict[str, Any]


class ReportSummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narrative: NonBlank = Field(max_length=1500)


class Report(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    kind: ReportKind
    subject_id: str
    subject_role: SubjectRole
    period_start: date
    period_end: date
    version: int
    numbers: dict[str, Any]
    numbers_sha256: str
    narrative: str | None
    narrative_status: NarrativeStatus
    narrative_note: str | None
    narrative_log_id: UUID | None
    generated_by: str
