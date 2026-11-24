"""Recurring failure patterns (phase 7.15, T1; plan docs/plans/phase-7.15.md, 2.1). Deterministic: no model call.

A failure is a rule flagging at least one row of a scheduled load that was loaded, released or held (a rerun is the same
file again and is not counted). A pattern is a feed, a rule and one dimension value (the weekday or the day of the month
the load was received, or the provider date the file is for) with at least `min_loads` failing loads in the window. Its
evidence is the dates; `loads_in_window` says how many of the feed's loads had that value, so "3 of 3" reads differently
from "3 of 20". Also: the groups referred to the provider (7.14), each once. Read only.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.repositories.configuration import MappingRepository
from core.repositories.exception_resolutions import ExceptionResolutionRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository

Dimension = Literal["weekday", "day_of_month", "provider_date"]
DIMENSIONS: tuple[Dimension, ...] = ("weekday", "day_of_month", "provider_date")
MIN_LOADS, WINDOW_DAYS = 3, 90  # the defaults of PATTERN_MIN_LOADS and PATTERN_WINDOW_DAYS


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


LOADS = text("""
SELECT id, received_at FROM loads
 WHERE feed_id = :feed AND purpose = 'scheduled' AND status IN ('loaded', 'released', 'held')
   AND rerun_of_load_id IS NULL AND received_at > :since AND received_at <= :until
 ORDER BY received_at, id
""")
FLAGS = text("""
SELECT sr.load_id, f.rule_key, count(*) FROM standard_records sr CROSS JOIN LATERAL unnest(sr.flags) AS f(rule_key)
 WHERE sr.load_id = ANY(:loads) GROUP BY sr.load_id, f.rule_key
""")
PROVIDER_DATES = text("""
SELECT load_id, max("values" ->> :element) FROM standard_records WHERE load_id = ANY(:loads) GROUP BY load_id
""")


def _date_element(session: Session, feed_id: UUID) -> str | None:
    """The feed's date-typed mapped element (the first by key): its value is the date the provider's file is for."""
    glossary = GlossaryRepository(session)
    keys = sorted(m.element_key for m in MappingRepository(session).current_for_feed(feed_id))
    return next((k for k in keys if glossary.get_current(k).data_type == "date"), None)


def _as_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _values(received: datetime, provider: date | None) -> dict[Dimension, str | None]:
    day = received.astimezone(UTC)
    return {"weekday": calendar.day_name[day.weekday()], "day_of_month": str(day.day),
            "provider_date": provider.isoformat() if provider else None}


def find_patterns(session: Session, *, feed_ids: Sequence[UUID], as_of: datetime, window_days: int = WINDOW_DAYS,
                  min_loads: int = MIN_LOADS) -> list[RulePattern]:
    """Read only. Most failing loads first, then feed code, rule key, dimension and value."""
    since = as_of - timedelta(days=window_days)
    found: list[RulePattern] = []
    for feed_id in dict.fromkeys(feed_ids):
        feed = FeedRepository(session).get(feed_id)
        loads = session.execute(LOADS, {"feed": feed_id, "since": since, "until": as_of}).all()
        if not loads:
            continue
        ids = [row.id for row in loads]
        element = _date_element(session, feed_id)
        provider = ({row[0]: _as_date(row[1]) for row in session.execute(PROVIDER_DATES, {"loads": ids, "element": element})}
                    if element else {})
        per_load = {row.id: _values(row.received_at, provider.get(row.id)) for row in loads}
        received = {row.id: row.received_at.astimezone(UTC).date() for row in loads}
        in_window: dict[tuple[str, str], int] = defaultdict(int)
        for values in per_load.values():
            for dimension, value in values.items():
                if value is not None:
                    in_window[(dimension, value)] += 1
        failing: dict[tuple[str, str, str], list[Evidence]] = defaultdict(list)
        for load_id, rule_key, flagged in session.execute(FLAGS, {"loads": ids}):
            for dimension, value in per_load[load_id].items():
                if value is not None:
                    failing[(rule_key, dimension, value)].append(Evidence(
                        load_id=load_id, received_on=received[load_id], provider_date=provider.get(load_id),
                        flagged_rows=flagged))
        for (rule_key, dimension, value), evidence in failing.items():
            if len(evidence) >= min_loads:
                found.append(RulePattern(
                    feed_id=feed_id, feed_code=feed.code, rule_key=rule_key, dimension=dimension, value=value,
                    failing_loads=len(evidence), loads_in_window=in_window[(dimension, value)],
                    evidence=sorted(evidence, key=lambda e: (e.received_on, str(e.load_id)))))
    return sorted(found, key=lambda p: (-p.failing_loads, p.feed_code, p.rule_key, p.dimension, p.value))


def referred_groups(session: Session, *, feed_ids: Sequence[UUID]) -> list[ReferredGroup]:
    """Read only. The groups referred to the provider (7.14), each once: the latest resolution of a feed's rule and pattern
    when it is a referral (a later waiver ends it)."""
    groups = []
    for feed_id in dict.fromkeys(feed_ids):
        feed = FeedRepository(session).get(feed_id)
        latest = {}
        for row in ExceptionResolutionRepository(session).list_for_feed(feed_id):
            latest[(row.rule_key, row.pattern)] = row
        groups += [ReferredGroup(feed_id=feed_id, feed_code=feed.code, rule_key=r.rule_key, pattern=r.pattern,
                                 load_id=r.load_id, reason=r.reason, decided_by=r.decided_by, referred_at=r.created_at)
                   for r in latest.values() if r.action == "referred"]
    return sorted(groups, key=lambda g: (g.feed_code, g.rule_key, g.pattern))
