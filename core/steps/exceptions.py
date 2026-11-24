"""Exceptions of a load (phase 7.14; plan docs/plans/phase-7.14.md, section 2.1). T1: the read side.

An exception is one flagged row of a load and one rule key in its flags (a row flagged by two rules is two). A group is
a rule key and a value pattern within one load: the shape of the values of the elements the rule names
(amaze_value_pattern(), migration 0033; core/engine/pattern.py is its twin). Groups are counted in SQL, so the number of
queries does not grow with the rows. Only a held load's groups take fixes (can_fix). Read only: nothing is written.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.domain.load import LoadPurpose, LoadStatus
from core.engine.run import DUPLICATE_RULE_KEY
from core.engine.run import _key_policy as engine_key_policy  # the one place the default key policy is decided
from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.feeds import FeedRepository
from core.repositories.loads import LoadRepository

SAMPLES = 5
MAX_PAGE = 50
GroupState = Literal["open"]  # T2 adds the states of a fix


class SampleRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    row_number: int
    values: dict[str, Any]


class ExceptionGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    group_key: str
    rule_key: str
    description: str | None  # the rule's current description; None for duplicate_key or a rule no longer current
    elements: list[str]  # the element keys the pattern is made of; [] means the pattern is (row)
    pattern: str
    exceptions: int
    samples: list[SampleRow]
    state: GroupState = "open"


class LoadExceptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    load_id: UUID
    feed_id: UUID
    status: LoadStatus
    can_fix: bool  # only a held load's groups take fixes (assumption 6)
    rows: int
    flagged_rows: int
    exceptions: int
    total_groups: int
    limit: int
    offset: int
    groups: list[ExceptionGroup]


class LoadExceptionCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    load_id: UUID
    received_at: datetime
    status: LoadStatus
    purpose: LoadPurpose
    rows: int
    flagged_rows: int
    exceptions: int


def group_key(rule_key: str, pattern: str) -> str:
    return hashlib.sha256(f"{rule_key}\x1f{pattern}".encode()).hexdigest()[:16]


def rule_elements(sql: str, element_keys: Sequence[str]) -> list[str]:
    """The element keys the rule's SQL names as whole words (any case), sorted. A heuristic (plan, risk 1)."""
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql)}
    return sorted(k for k in set(element_keys) if k.lower() in words)


def _elements_by_rule(session: Session, feed_id: UUID, loaded: Sequence[str]) -> tuple[dict[str, list[str]],
                                                                                       dict[str, str]]:
    rules = RuleRepository(session).current_for_feed(feed_id)
    elements = {r.rule_key: rule_elements(r.sql, loaded) for r in rules}
    descriptions = {r.rule_key: r.description for r in rules}
    policy = engine_key_policy(session, feed_id)
    if policy is not None:
        by_field = {m.source_field.lower(): m.element_key for m in MappingRepository(session).current_for_feed(feed_id)}
        elements[DUPLICATE_RULE_KEY] = sorted({by_field[c.lower()] for c in policy.key_columns if c.lower() in by_field})
    return elements, descriptions


GROUPS = text("""
WITH ex AS (
    SELECT sr.row_number, sr."values", f.rule_key
      FROM standard_records sr CROSS JOIN LATERAL unnest(sr.flags) AS f(rule_key)
     WHERE sr.load_id = :load AND (CAST(:rule AS text) IS NULL OR f.rule_key = CAST(:rule AS text))
), g AS (
    SELECT rule_key, row_number,
           amaze_value_pattern("values", ARRAY(SELECT jsonb_array_elements_text(CAST(:elements AS jsonb) -> rule_key)))
               AS pattern
      FROM ex
)
SELECT rule_key, pattern, count(*) AS n, (array_agg(row_number ORDER BY row_number))[1:%d] AS samples,
       count(*) OVER () AS total
  FROM g GROUP BY rule_key, pattern
 ORDER BY n DESC, rule_key COLLATE "C", pattern COLLATE "C"
 LIMIT :limit OFFSET :offset
""" % SAMPLES)

TOTALS = text("""
SELECT count(*), count(*) FILTER (WHERE cardinality(flags) > 0), coalesce(sum(cardinality(flags)), 0)
  FROM standard_records WHERE load_id = :load
""")


def load_exceptions(session: Session, load_id: UUID, *, rule: str | None = None, limit: int = MAX_PAGE,
                    offset: int = 0) -> LoadExceptions:
    """Read only. LoadNotFound. Groups largest first (then rule key and pattern), `limit` at most MAX_PAGE."""
    limit = max(1, min(limit, MAX_PAGE))
    load = LoadRepository(session).get(load_id)
    rows, flagged, exceptions = session.execute(TOTALS, {"load": load_id}).one()
    first = session.scalar(text('SELECT "values" FROM standard_records WHERE load_id = :load '
                                "ORDER BY row_number LIMIT 1"), {"load": load_id})
    elements, descriptions = _elements_by_rule(session, load.feed_id, list(first or {}))
    found = session.execute(GROUPS, {"load": load_id, "rule": rule, "elements": json.dumps(elements),
                                     "limit": limit, "offset": max(0, offset)}).all()
    wanted = sorted({n for g in found for n in g.samples})
    values = dict(session.execute(text('SELECT row_number, "values" FROM standard_records '
                                       "WHERE load_id = :load AND row_number = ANY(:rows)"),
                                  {"load": load_id, "rows": wanted}).all()) if wanted else {}
    groups = [ExceptionGroup(group_key=group_key(g.rule_key, g.pattern), rule_key=g.rule_key,
                             description=descriptions.get(g.rule_key), elements=elements.get(g.rule_key, []),
                             pattern=g.pattern, exceptions=g.n,
                             samples=[SampleRow(row_number=n, values=values[n]) for n in g.samples])
              for g in found]
    total = found[0].total if found else _count_groups(session, load_id, rule, elements, offset)
    return LoadExceptions(load_id=load.id, feed_id=load.feed_id, status=load.status, can_fix=load.status == "held",
                          rows=rows, flagged_rows=flagged, exceptions=exceptions, total_groups=total, limit=limit,
                          offset=max(0, offset), groups=groups)


def _count_groups(session: Session, load_id: UUID, rule: str | None, elements: dict, offset: int) -> int:
    """A page past the last group has no row to carry the total; count it (only then)."""
    if offset <= 0:
        return 0
    found = session.execute(GROUPS, {"load": load_id, "rule": rule, "elements": json.dumps(elements),
                                     "limit": 1, "offset": 0}).all()
    return found[0].total if found else 0


def feed_exceptions(session: Session, feed_id: UUID) -> list[LoadExceptionCounts]:
    """Read only. FeedNotFound. The feed's loads, newest first, each with its rows, flagged rows and exceptions."""
    FeedRepository(session).get(feed_id)
    loads = LoadRepository(session).list_for_feed(feed_id)
    if not loads:
        return []
    counts = {row[0]: row[1:] for row in session.execute(text("""
        SELECT load_id, count(*), count(*) FILTER (WHERE cardinality(flags) > 0), coalesce(sum(cardinality(flags)), 0)
          FROM standard_records WHERE load_id = ANY(:loads) GROUP BY load_id"""), {"loads": [l.id for l in loads]})}
    return [LoadExceptionCounts(load_id=l.id, received_at=l.received_at, status=l.status, purpose=l.purpose,
                                rows=counts.get(l.id, (0, 0, 0))[0], flagged_rows=counts.get(l.id, (0, 0, 0))[1],
                                exceptions=counts.get(l.id, (0, 0, 0))[2])
            for l in sorted(loads, key=lambda l: (l.received_at, l.created_at), reverse=True)]
