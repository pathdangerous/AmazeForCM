"""ReportRepository (phase 7.15): reports are append-only and versioned. The next version of a report (kind, subject,
role, period) is read only under the advisory lock lock(); the database refuses any other (migration 0035)."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.domain.report import Report
from core.repositories.tables import ReportRow as Row


class ReportNotFound(LookupError):
    """No report has this id."""


class ReportRepository:
    def __init__(self, session: Session):
        self._session = session

    def lock(self, kind: str, subject_id: str, role: str, period_start: date) -> None:
        """One writer at a time per report, so two writers cannot both take the next version."""
        self._session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                              {"k": f"report:{kind}:{subject_id}:{role}:{period_start}"})

    def latest(self, kind: str, subject_id: str, role: str, period_start: date) -> Report | None:
        row = self._session.scalars(select(Row).where(
            Row.kind == kind, Row.subject_id == subject_id, Row.subject_role == role, Row.period_start == period_start)
            .order_by(Row.version.desc()).limit(1)).first()
        return None if row is None else Report.model_validate(row)

    def add(self, *, kind: str, subject_id: str, role: str, period_start: date, period_end: date, numbers: dict[str, Any],
            narrative: str | None, narrative_status: str, narrative_note: str | None, narrative_log_id: UUID | None,
            generated_by: str) -> Report:
        """The next version. Call lock() first."""
        version = (self._session.scalar(select(func.max(Row.version)).where(
            Row.kind == kind, Row.subject_id == subject_id, Row.subject_role == role,
            Row.period_start == period_start)) or 0) + 1
        row = Row(kind=kind, subject_id=subject_id, subject_role=role, period_start=period_start, period_end=period_end,
                  version=version, numbers=numbers, narrative=narrative, narrative_status=narrative_status,
                  narrative_note=narrative_note, narrative_log_id=narrative_log_id, generated_by=generated_by)
        self._session.add(row)
        self._session.flush()
        self._session.refresh(row)  # numbers_sha256 was set by the database
        return Report.model_validate(row)

    def get(self, report_id: UUID) -> Report:
        row = self._session.get(Row, report_id)
        if row is None:
            raise ReportNotFound(f"No report with id {report_id}")
        return Report.model_validate(row)

    def list_for(self, subject_id: str, role: str | None = None, *, limit: int = 100) -> list[Report]:
        """Newest period first, then the highest version."""
        query = select(Row).where(Row.subject_id == subject_id)
        if role is not None:
            query = query.where(Row.subject_role == role)
        query = query.order_by(Row.period_start.desc(), Row.kind, Row.version.desc()).limit(limit)
        return [Report.model_validate(r) for r in self._session.scalars(query)]
