"""Reports per data owner and data steward (phase 7.15, T2; plan docs/plans/phase-7.15.md, 2.2).

build_numbers() counts every figure from the tables, in code: no model writes or changes one. report_input() reads (the
numbers, and the report already there) and the caller ends that transaction; write_report() then calls the narrative agent
with no connection held (the model-call pattern of CLAUDE.md), locks the report, and writes the next version in one
transaction. The narrative is separate and optional: a narrative that states a figure the report does not hold is refused
(narrative_refused: the report is stored without it, with the figure noted), a model that is down gives no_narrative, and
no agent gives not_asked; the report is stored in each case. A report the jobs already made is report_exists; a person
asks for a new version explicitly. The steps flush; the caller commits.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.domain.pattern import RulePattern
from core.domain.report import (
    DecisionNumbers,
    ExceptionNumbers,
    FeedNumbers,
    Period,
    PeriodNumbers,
    ReportNumbers,
    ReportSummaryInput,
    RerunNumbers,
    SubjectRole,
    Totals,
)
from core.repositories.reports import ReportRepository  # noqa: F401 (the step's own name for it)
from core.repositories.exception_resolutions import ExceptionResolutionRepository  # noqa: F401
from core.steps.exceptions import load_exceptions
from core.steps.patterns import analyse_failures, referred_groups
from core.steps.result import StepResult
from core.steps.s1_scope import RunsAgent
from ports.config import Settings
from ports.notify.base import Notifier

OPEN_STATES = ("open", "fix_pending")
FIGURE = re.compile(r"\d+(?:\.\d+)?")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
NUMBER_LEAF = re.compile(r"-?\d+(?:\.\d+)?")


def _start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def subjects(session: Session, *, stewards: Sequence[str]) -> list[tuple[str, SubjectRole]]:
    """Who gets a report: every person named in a registered or paused feed's latest registration (data owner), in
    alphabetical order, then each data steward as the identity port lists them."""
    owners = session.execute(text("""
        SELECT DISTINCT r.owner_id FROM feeds f JOIN LATERAL (SELECT owner_id FROM feed_registrations
              WHERE feed_id = f.id ORDER BY version DESC LIMIT 1) r ON true
         WHERE f.stage IN ('registered', 'paused') ORDER BY r.owner_id""")).scalars().all()
    return [*[(o, "data_owner") for o in owners], *[(s, "data_steward") for s in dict.fromkeys(stewards)]]


def _feeds_of(session: Session, subject_id: str, role: SubjectRole) -> list[tuple[UUID, str]]:
    rows = session.execute(text("""
        SELECT f.id, f.code FROM feeds f JOIN LATERAL (SELECT owner_id FROM feed_registrations
              WHERE feed_id = f.id ORDER BY version DESC LIMIT 1) r ON true
         WHERE f.stage IN ('registered', 'paused') AND (:role = 'data_steward' OR r.owner_id = :subject)
         ORDER BY f.code"""), {"role": role, "subject": subject_id}).all()
    return [(row[0], row[1]) for row in rows]


def _round(value: Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(value).quantize(Decimal("0.1"))


def _feed_numbers(session: Session, feed_id: UUID, code: str, period: Period, patterns: list[RulePattern],
                  referred: int) -> FeedNumbers:
    window = {"f": feed_id, "s": _start(period.start), "e": _start(period.end)}
    by_status = dict(session.execute(text(
        "SELECT status, count(*) FROM loads WHERE feed_id = :f AND purpose = 'scheduled' AND received_at >= :s "
        "AND received_at < :e GROUP BY status"), window).all())
    rows, flagged, waived, average = session.execute(text(
        "SELECT coalesce(sum(c.rows), 0), coalesce(sum(c.flagged_rows), 0), coalesce(sum(c.waived_rows), 0), "
        "avg(c.trust_score) FROM load_certifications c JOIN loads l ON l.id = c.load_id "
        "WHERE l.feed_id = :f AND l.received_at >= :s AND l.received_at < :e"), window).one()
    resolved = dict(session.execute(text(
        "SELECT origin, count(*) FROM proposals WHERE feed_id = :f AND status = 'approved' AND decided_at >= :s "
        "AND decided_at < :e AND (kind = 'exception_fix' OR (kind = 'transformation' AND payload ? 'exception_group')) "
        "GROUP BY origin"), window).all())
    waivers = session.scalar(text(
        "SELECT count(*) FROM exception_resolutions WHERE feed_id = :f AND action = 'waived' AND created_at >= :s "
        "AND created_at < :e"), window)
    reruns = dict(session.execute(text(
        "SELECT status, count(*) FROM loads WHERE feed_id = :f AND rerun_of_load_id IS NOT NULL AND received_at >= :s "
        "AND received_at < :e GROUP BY status"), window).all())
    drift = session.scalar(text(
        "SELECT count(*) FROM loads WHERE feed_id = :f AND purpose = 'scheduled' AND received_at >= :s "
        "AND received_at < :e AND arrival_detail -> 'drift' IS NOT NULL"), window)
    open_now = waiting = 0
    for (load_id,) in session.execute(text(  # held loads no released rerun has answered
            "SELECT l.id FROM loads l WHERE l.feed_id = :f AND l.status = 'held' AND l.purpose = 'scheduled' "
            "AND NOT EXISTS (SELECT 1 FROM loads r WHERE r.rerun_of_load_id = l.id AND r.status = 'released') "
            "ORDER BY l.received_at, l.id"), window):
        offset = 0
        while True:
            page = load_exceptions(session, load_id, limit=50, offset=offset)
            open_now += sum(g.state in OPEN_STATES for g in page.groups)
            waiting += sum(g.state == "waiting_for_owner" for g in page.groups)
            offset += 50
            if offset >= page.total_groups:
                break
    return FeedNumbers(
        feed_id=feed_id, feed_code=code, loads=sum(by_status.values()), released=by_status.get("released", 0),
        held=by_status.get("held", 0), failed=by_status.get("failed_rolled_back", 0),
        quarantined=by_status.get("quarantined", 0), rows=rows, flagged_rows=flagged, waived_rows=waived,
        average_trust_score=_round(average), open_now=open_now, waiting_for_owner=waiting,
        resolved_by_agent=resolved.get("agent", 0), resolved_by_hand=resolved.get("manual", 0), waivers=waivers,
        referred_groups=referred, reruns_released=reruns.get("released", 0), reruns_still_held=reruns.get("held", 0),
        drift_events=drift, patterns=len(patterns))


def build_numbers(session: Session, *, subject_id: str, role: SubjectRole, period: Period,
                  settings: Settings | None = None) -> ReportNumbers:
    """Read only. Every figure of the report, counted from the tables. A feed with nothing in the period is still listed
    (with zeros), so a quiet day is a report and not a missing one."""
    settings = settings or Settings(_env_file=None)
    feeds = _feeds_of(session, subject_id, role)
    ids = [feed_id for feed_id, _ in feeds]
    patterns, always_failing = analyse_failures(session, feed_ids=ids, as_of=_start(period.end),
                                                window_days=settings.pattern_window_days, min_loads=settings.pattern_min_loads)
    referred = referred_groups(session, feed_ids=ids)
    per_feed = [_feed_numbers(session, feed_id, code, period, [p for p in patterns if p.feed_id == feed_id],
                              sum(g.feed_id == feed_id for g in referred)) for feed_id, code in feeds]
    average = session.scalar(text(
        "SELECT avg(c.trust_score) FROM load_certifications c JOIN loads l ON l.id = c.load_id "
        "WHERE l.feed_id = ANY(:ids) AND l.received_at >= :s AND l.received_at < :e"),
        {"ids": ids, "s": _start(period.start), "e": _start(period.end)})
    decisions = None
    if role == "data_steward":
        found = session.execute(text(
            "SELECT a.decision, p.kind, count(*) FROM approvals a JOIN proposals p ON p.id = a.proposal_id "
            "WHERE a.decided_by = :who AND a.created_at >= :s AND a.created_at < :e GROUP BY a.decision, p.kind"),
            {"who": subject_id, "s": _start(period.start), "e": _start(period.end)}).all()
        by_kind: dict[str, int] = {}
        for decision, kind, n in found:
            if decision == "approved":
                by_kind[kind] = by_kind.get(kind, 0) + n
        decisions = DecisionNumbers(approved=sum(n for d, _, n in found if d == "approved"),
                                    rejected=sum(n for d, _, n in found if d == "rejected"), by_kind=dict(sorted(by_kind.items())))
    total = lambda name: sum(getattr(f, name) for f in per_feed)  # noqa: E731
    return ReportNumbers(
        subject_id=subject_id, subject_role=role,
        period=PeriodNumbers(kind=period.kind, start=period.start.isoformat(), end=period.end.isoformat()),
        totals=Totals(feeds=len(per_feed), loads=total("loads"), released=total("released"), held=total("held"),
                      failed=total("failed"), quarantined=total("quarantined"), rows=total("rows"),
                      flagged_rows=total("flagged_rows"), waived_rows=total("waived_rows"),
                      average_trust_score=_round(average)),
        feeds=per_feed,
        exceptions=ExceptionNumbers(open_now=total("open_now"), waiting_for_owner=total("waiting_for_owner"),
                                    resolved_by_agent=total("resolved_by_agent"), resolved_by_hand=total("resolved_by_hand")),
        waivers=total("waivers"), reruns=RerunNumbers(released=total("reruns_released"), still_held=total("reruns_still_held")),
        drift_events=total("drift_events"), patterns=patterns, always_failing=always_failing, referred_groups=referred,
        decisions=decisions)


# ---------------------------------------------------------------- the narrative check


def _numeric_leaves(value: Any, found: set[Decimal], dates: set[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        found.add(Decimal(str(value)))
    elif isinstance(value, str):
        if ISO_DATE.fullmatch(value):
            dates.add(value)
        elif NUMBER_LEAF.fullmatch(value):
            found.add(Decimal(value))
    elif isinstance(value, dict):
        for item in value.values():
            _numeric_leaves(item, found, dates)
    elif isinstance(value, list):
        for item in value:
            _numeric_leaves(item, found, dates)


def figure_not_in_numbers(narrative: str, numbers: dict[str, Any]) -> str | None:
    """The first figure the narrative states that the report does not hold, or None. A date is held when the report
    names it; any other figure (digits, a decimal point allowed) is held when a number of the report equals it. A check
    of figures, not of meaning: the report page shows the numbers beside the narrative."""
    held: set[Decimal] = set()
    dates: set[str] = set()
    _numeric_leaves(numbers, held, dates)
    for stated in ISO_DATE.findall(narrative):
        if stated not in dates:
            return stated
    for figure in FIGURE.findall(ISO_DATE.sub(" ", narrative)):
        if Decimal(figure) not in held:
            return figure
    return None


# ---------------------------------------------------------------- write


class ReportWritten(StepResult):
    outcome: Literal["report_written"] = "report_written"
    report_id: UUID
    version: int


class ReportWithoutNarrative(StepResult):
    outcome: Literal["report_written_without_narrative"] = "report_written_without_narrative"
    report_id: UUID
    version: int
    reason: Literal["narrative_refused", "no_narrative", "not_asked"]
    figure: str | None = None  # narrative_refused: the first figure the report does not hold


class ReportExists(StepResult):
    outcome: Literal["report_exists"] = "report_exists"
    report_id: UUID
    version: int


ReportResult = Annotated[ReportWritten | ReportWithoutNarrative | ReportExists, Field(discriminator="outcome")]


class ReportPlan(BaseModel):
    """What one report needs, read in a transaction the caller then ends."""

    model_config = ConfigDict(frozen=True)

    subject_id: str
    role: SubjectRole
    period: Period
    numbers: dict[str, Any]  # build_numbers(), as JSON: what is stored and what the narrative is checked against
    summary_input: ReportSummaryInput
    existing: UUID | None  # the report already there (any version), for the jobs to skip


def report_input(session: Session, *, subject_id: str, role: SubjectRole, period: Period,
                 settings: Settings | None = None) -> ReportPlan:
    """Read only."""
    numbers = build_numbers(session, subject_id=subject_id, role=role, period=period, settings=settings)
    stored = numbers.model_dump(mode="json")
    shown = {**stored, "patterns": [{k: v for k, v in p.items() if k != "evidence"} for p in stored["patterns"]]}
    latest = ReportRepository(session).latest(period.kind, subject_id, role, period.start)
    return ReportPlan(subject_id=subject_id, role=role, period=period, numbers=stored, existing=latest.id if latest else None,
                      summary_input=ReportSummaryInput(subject_role=role, kind=period.kind, numbers=shown))


def tell_subject(notifier: Notifier, *, plan: ReportPlan, result: ReportResult) -> None:
    """Plan, 2.3: the subject is told through the Notifier (in-app; email stays a stub) with a link to their report."""
    label = plan.period.start.isoformat() if plan.period.kind == "daily" else f"{plan.period.start:%Y-%m}"
    totals, exceptions = plan.numbers["totals"], plan.numbers["exceptions"]
    notifier.notify(recipient=plan.subject_id, subject=f"{plan.period.kind.capitalize()} report: {label}",
                    body=f"{totals['loads']} loads, {totals['held']} held, {exceptions['open_now']} exception groups open.",
                    link=f"#/reports/{result.report_id}")


def write_report(session: Session, *, plan: ReportPlan, agent: RunsAgent | None, generated_by: str,
                 new_version: bool = False, no_narrative_on: tuple[type[Exception], ...] = ()) -> ReportResult:
    """The caller has ended its read transaction (Agent.run() refuses one). The jobs skip a report that exists; a person
    asks for a new version. The numbers are the plan's, built before the call: nothing the model says touches them.
    `no_narrative_on`: the errors that mean the model could not answer (the caller names them, as core/steps never imports
    ports.llm); the report is then stored without a narrative."""
    repository = ReportRepository(session)
    kind, start = plan.period.kind, plan.period.start
    if plan.existing is not None and not new_version:
        return ReportExists(report_id=plan.existing, version=repository.get(plan.existing).version)
    status, narrative, note, log_id, reason, figure = "none", None, None, None, "not_asked", None
    if agent is not None:
        try:
            run = agent.run(session, plan.summary_input,
                            before_write=lambda s: repository.lock(kind, plan.subject_id, plan.role, start))
        except no_narrative_on:
            reason = "no_narrative"
        else:
            log_id = run.log_id
            figure = figure_not_in_numbers(run.output.narrative, plan.numbers)
            if figure is None:
                status, narrative, reason = "written", run.output.narrative.strip(), "written"
            else:
                status, reason = "refused", "narrative_refused"
                note = f"The narrative stated {figure}, which is no number of this report; it was not kept."
    repository.lock(kind, plan.subject_id, plan.role, start)
    latest = repository.latest(kind, plan.subject_id, plan.role, start)
    if latest is not None and not new_version:  # a job of ours finished first
        return ReportExists(report_id=latest.id, version=latest.version)
    report = repository.add(kind=kind, subject_id=plan.subject_id, role=plan.role, period_start=start,
                            period_end=plan.period.end, numbers=plan.numbers, narrative=narrative,
                            narrative_status=status, narrative_note=note, narrative_log_id=log_id,
                            generated_by=generated_by)
    if status == "written":
        return ReportWritten(report_id=report.id, version=report.version)
    return ReportWithoutNarrative(report_id=report.id, version=report.version, reason=reason, figure=figure)
