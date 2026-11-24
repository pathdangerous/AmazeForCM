"""The monthly audit view (phase 7.15, T4; plan docs/plans/phase-7.15.md, 2.4). Read only.

One row per decision of the month (an approvals row: who, role, decision, note) with its item, its feed, who made the item
(an agent; a person; rules) and, for a decision by `system`, system; the code version of the feed in force at that moment
(the latest code_versions row at or before the decision); whether it is a waiver. And one row per rerun of a held load: who
started it (None before 0035 recorded it), why, the load it reruns, the code version its go-live pinned. And, from phase 7.16,
one row per erase of profiling history (who, why, the versions erased). Filters: feed and
person. The CSV is the same rows; a cell that starts like a formula (= + - @) is prefixed with a quote so a spreadsheet
never runs it. Months are UTC calendar months.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.orm import Session

MONTH = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
CSV_COLUMNS = ("time", "kind", "feed", "who", "role", "decision", "item", "summary", "by", "agent", "code_version", "waiver",
               "rerun_of", "note")
SUMMARY_KEYS = ("summary", "reason", "source_field", "rule_key", "column")


class AuditRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: datetime
    kind: Literal["decision", "rerun", "erasure"]
    feed_id: UUID | None
    feed_code: str | None
    who: str | None  # None: a rerun made before 0035 recorded who
    role: str | None
    decision: Literal["approved", "rejected", "started", "erased"]
    item: str  # the proposal's kind, "load" for a rerun, "profiles" for an erase (phase 7.16)
    summary: str
    by: Literal["agent", "manual", "rule", "system"]  # who made the item; `system` when system decided
    agent: str | None
    code_version: int | None
    waiver: bool
    rerun_of: UUID | None
    note: str | None
    proposal_id: UUID | None = None
    load_id: UUID | None = None


DECISIONS = text("""
SELECT a.created_at AS at, a.decided_by, a.role, a.decision, a.note, p.id AS proposal_id, p.kind, p.payload, p.origin,
       p.feed_id, f.code AS feed_code, pl.agent_name,
       (SELECT c.version FROM code_versions c WHERE c.feed_id = p.feed_id AND c.created_at <= a.created_at
         ORDER BY c.version DESC LIMIT 1) AS code_version
  FROM approvals a JOIN proposals p ON p.id = a.proposal_id
  LEFT JOIN feeds f ON f.id = p.feed_id LEFT JOIN proposal_log pl ON pl.id = p.proposal_log_id
 WHERE a.created_at >= :start AND a.created_at < :end
   AND (CAST(:feed AS uuid) IS NULL OR p.feed_id = CAST(:feed AS uuid))
   AND (CAST(:person AS text) IS NULL OR a.decided_by = CAST(:person AS text))
 ORDER BY a.created_at, p.created_at, a.step, a.id
""")

RERUNS = text("""
SELECT l.received_at AS at, l.id AS load_id, l.feed_id, f.code AS feed_code, l.rerun_by, l.rerun_reason, l.rerun_of_load_id,
       (SELECT c.version FROM feed_go_lives g JOIN code_versions c ON c.id = g.code_version_id WHERE g.id = l.go_live_id)
         AS code_version
  FROM loads l JOIN feeds f ON f.id = l.feed_id
 WHERE l.rerun_of_load_id IS NOT NULL AND l.received_at >= :start AND l.received_at < :end
   AND (CAST(:feed AS uuid) IS NULL OR l.feed_id = CAST(:feed AS uuid))
   AND (CAST(:person AS text) IS NULL OR l.rerun_by = CAST(:person AS text))
 ORDER BY l.received_at, l.id
""")


ERASURES = text("""
SELECT e.at, e.feed_id, e.feed_code, e."by", e.reason, e.erased_versions,
       (SELECT c.version FROM code_versions c WHERE c.feed_id = e.feed_id AND c.created_at <= e.at
         ORDER BY c.version DESC LIMIT 1) AS code_version
  FROM profile_erasures e
 WHERE e.at >= :start AND e.at < :end
   AND (CAST(:feed AS uuid) IS NULL OR e.feed_id = CAST(:feed AS uuid))
   AND (CAST(:person AS text) IS NULL OR e."by" = CAST(:person AS text))
 ORDER BY e.at, e.id
""")


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """ValueError for anything but YYYY-MM."""
    found = MONTH.fullmatch(month)
    if found is None:
        raise ValueError(f"A month is written YYYY-MM, not {month!r}")
    year, number = int(found[1]), int(found[2])
    first, after = date(year, number, 1), date(year + (number == 12), number % 12 + 1, 1)
    return datetime.combine(first, time.min, tzinfo=UTC), datetime.combine(after, time.min, tzinfo=UTC)


def _summary(kind: str, payload: dict) -> str:
    return next((payload[k] for k in SUMMARY_KEYS if isinstance(payload.get(k), str) and payload[k]), kind.replace("_", " "))


def audit_rows(session: Session, *, month: str, feed_id: UUID | None = None, person: str | None = None) -> list[AuditRow]:
    """Read only. ValueError (a bad month). Time order; ties by proposal and step."""
    start, end = month_bounds(month)
    params = {"start": start, "end": end, "feed": feed_id, "person": person}
    rows: list[AuditRow] = []
    for r in session.execute(DECISIONS, params):
        waiver = r.kind == "exception_fix" and r.payload.get("action") == "waive"
        rows.append(AuditRow(
            at=r.at, kind="decision", feed_id=r.feed_id, feed_code=r.feed_code, who=r.decided_by, role=r.role,
            decision=r.decision, item=r.kind, summary=_summary(r.kind, r.payload),
            by="system" if r.decided_by == "system" else ("agent" if r.origin == "agent" else r.origin),
            agent=r.agent_name, code_version=r.code_version, waiver=waiver, rerun_of=None, note=r.note,
            proposal_id=r.proposal_id))
    for r in session.execute(RERUNS, params):
        rows.append(AuditRow(
            at=r.at, kind="rerun", feed_id=r.feed_id, feed_code=r.feed_code, who=r.rerun_by, role=None, decision="started",
            item="load", summary="Rerun of a held load's file", by="manual", agent=None, code_version=r.code_version,
            waiver=False, rerun_of=r.rerun_of_load_id, note=r.rerun_reason, load_id=r.load_id))
    for r in session.execute(ERASURES, params):  # phase 7.16: an erase of profiling history is a decision too
        rows.append(AuditRow(
            at=r.at, kind="erasure", feed_id=r.feed_id, feed_code=r.feed_code, who=r.by, role=None, decision="erased",
            item="profiles", summary="Erased profile versions " + ", ".join(map(str, r.erased_versions)), by="manual",
            agent=None, code_version=r.code_version, waiver=False, rerun_of=None, note=r.reason))
    return sorted(rows, key=lambda row: row.at)  # stable: decisions keep their query order within a time


def _safe(value: object) -> str:
    cell = "" if value is None else str(value)
    return "'" + cell if cell[:1] in ("=", "+", "-", "@", "\t", "\r") else cell


def audit_csv(rows: list[AuditRow]) -> str:
    """RFC 4180, UTF-8 text, one header row. A cell that starts like a formula is quoted with a leading apostrophe."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([_safe(v) for v in (
            r.at.astimezone(UTC).isoformat(), r.kind, r.feed_code, r.who if r.who is not None else "not recorded", r.role,
            r.decision, r.item, r.summary, r.by, r.agent, r.code_version, "yes" if r.waiver else "no", r.rerun_of, r.note)])
    return out.getvalue()
