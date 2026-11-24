"""Phase 7.15: the reports of a data owner or a data steward (plan docs/plans/phase-7.15.md, 2.2).

Any backend role reads them (like the Overview). A person makes a new version of their own report now; operations may for
anyone. The report is built as every model-call step is: read the numbers, end the transaction, call the narrative agent
with no connection held, then lock and write. The numbers are code's; the narrative is a separate field.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from agents.report_summariser import ReportSummariserAgent
from apps.api.deps import backend_user, get_app_settings, get_db_session, get_report_agent
from apps.api.transactions import in_transaction, read
from core.domain.report import Period, Report, ReportKind, ReportNumbers, SubjectRole
from core.repositories.reports import ReportNotFound, ReportRepository
from core.steps.reports import ReportResult, build_numbers, report_input, write_report
from ports.config import Settings
from ports.identity.base import User
from ports.llm.base import LLMRefused, LLMUnavailable

NO_NARRATIVE_ON = (LLMUnavailable, LLMRefused)  # the model could not answer: the report is stored without a narrative
router = APIRouter(prefix="/api/amaze", tags=["reports"], dependencies=[Depends(backend_user)])
DbSession = Annotated[Session, Depends(get_db_session)]
BackendUser = Annotated[User, Depends(backend_user)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
ReportAgent = Annotated[ReportSummariserAgent, Depends(get_report_agent)]


class ReportRow(BaseModel):
    """One line of the list: what a report is, not its numbers."""

    id: UUID
    created_at: datetime
    kind: ReportKind
    subject_id: str
    subject_role: SubjectRole
    period_start: date
    period_end: date
    version: int
    narrative_status: str
    generated_by: str


def _row(report: Report) -> ReportRow:
    return ReportRow(**report.model_dump(include=set(ReportRow.model_fields)))


@router.get("/reports", response_model=list[ReportRow])
def list_reports(session: DbSession, user: BackendUser, subject: str | None = None,
                 role: SubjectRole | None = None) -> list[ReportRow]:
    """The subject's reports (default: the signed-in person's), newest period first, then the highest version."""
    return read(session, lambda: [_row(r) for r in ReportRepository(session).list_for(subject or user.id, role)])


@router.get("/reports/preview", response_model=ReportNumbers)
def preview_report(session: DbSession, user: BackendUser, settings: AppSettings, role: SubjectRole,
                   subject: str | None = None) -> ReportNumbers:
    """Phase 7.16: today so far, counted like a report and never stored: no reports row, no notice, no model call (so
    no narrative). Own view, or operations for anyone. Declared before /reports/{report_id}."""
    who = subject or user.id
    if who != user.id and "operations" not in user.roles:
        raise HTTPException(status_code=403, detail="Only operations previews another person's report")
    period = Period.daily(datetime.now(UTC).date())
    return read(session, lambda: build_numbers(session, subject_id=who, role=role, period=period, settings=settings))


@router.get("/reports/{report_id}", response_model=Report)
def one_report(report_id: UUID, session: DbSession) -> Report:
    try:
        return read(session, lambda: ReportRepository(session).get(report_id))
    except ReportNotFound:
        raise HTTPException(status_code=404, detail="Not found") from None


class MakeReportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ReportKind
    period_start: date  # a daily report's day; a monthly report's first day
    role: SubjectRole
    subject_id: str | None = None  # default: the signed-in person


class MakeReportResponse(BaseModel):
    result: ReportResult


@router.post("/reports", response_model=MakeReportResponse)
def make_report(body: MakeReportBody, session: DbSession, user: BackendUser, agent: ReportAgent,
                settings: AppSettings) -> MakeReportResponse:
    """A new version of a report now (a job never makes a second one). Own report, or operations for anyone."""
    subject = body.subject_id or user.id
    if subject != user.id and "operations" not in user.roles:
        raise HTTPException(status_code=403, detail="Only operations asks for another person's report")
    if body.kind == "monthly" and body.period_start.day != 1:
        raise HTTPException(status_code=422, detail="A monthly report starts on the first day of a month")
    period = (Period.daily(body.period_start) if body.kind == "daily"
              else Period.monthly(body.period_start.year, body.period_start.month))
    if period.end > datetime.now(UTC).date():
        raise HTTPException(status_code=422, detail="A report is of a period that has ended")
    plan = read(session, lambda: report_input(session, subject_id=subject, role=body.role, period=period,
                                              settings=settings))  # ends the read transaction
    result = in_transaction(session, lambda: write_report(session, plan=plan, agent=agent, generated_by=user.id,
                                                          new_version=True, no_narrative_on=NO_NARRATIVE_ON))
    return MakeReportResponse(result=result)
