"""Step 15, certify (phase 7 plan, assumptions 19 to 23; phase 6 note 2).

Outcomes (CLAUDE.md): released, held_consumers_warned. Rules execute: no agent, no model call. The trust score is
100 x (records with no flag) / records, to one decimal, from standard_records.flags (the same count the database
recounts, G14); the validated stage's per-rule counts are kept with it. At or above the threshold (configuration,
TRUST_RELEASE_THRESHOLD) the load is released; below it the load is held and its consumers, operations and the
backend team are told. A held load stays held: nobody releases it, and the next delivery is certified on its own.

certify() runs inside the engine's savepoint (core/engine/run.py `then`), after the load is `loaded`. A refusal by
the database (G13, G14) is raised as AfterLoadRefused("certify", ...), which fails the load naming this step.
The step flushes; the caller commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from core.domain.load import Load
from core.engine.run import AfterLoadRefused
from core.repositories.certifications import LoadCertificationRepository
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feeds import FeedRepository
from core.repositories.loads import LoadRepository
from core.repositories.standard_records import StandardRecordRepository
from core.steps.result import StepResult
from core.steps.s12_arrival import consumers_of
from ports.notify.base import Notifier


class Released(StepResult):
    outcome: Literal["released"] = "released"
    load_id: UUID
    trust_score: Decimal
    threshold: Decimal
    rows: int
    flagged_rows: int


class HeldConsumersWarned(StepResult):
    outcome: Literal["held_consumers_warned"] = "held_consumers_warned"
    load_id: UUID
    trust_score: Decimal
    threshold: Decimal
    rows: int
    flagged_rows: int
    warned: list[str]  # the user ids told


CertifyResult = Annotated[Released | HeldConsumersWarned, Field(discriminator="outcome")]


def trust_score(rows: int, flagged: int) -> Decimal:
    """100 x unflagged / rows, one decimal, halves up (as the database rounds); a load of no record scores 100.0."""
    if rows == 0:
        return Decimal("100.0")
    return (Decimal(100) * (rows - flagged) / rows).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def certify(session: Session, load: Load, *, threshold: Decimal, notifier: Notifier, operations: Sequence[str],
            backend_team: Sequence[str]) -> Released | HeldConsumersWarned:
    """Certify a loaded scheduled load: write the certification, move the load to released or held, and (held)
    warn people. AfterLoadRefused('certify', ...) if the database refuses any of it."""
    try:
        return _certify(session, load, threshold, notifier, operations, backend_team)
    except DBAPIError as exc:
        raise AfterLoadRefused("certify", str(exc.orig).splitlines()[0]) from exc


def _certify(session: Session, load: Load, threshold: Decimal, notifier: Notifier, operations: Sequence[str],
             backend_team: Sequence[str]) -> Released | HeldConsumersWarned:
    records = StandardRecordRepository(session)
    rows, flagged = records.count(load.id), records.count_flagged(load.id)
    score = trust_score(rows, flagged)
    threshold = threshold.quantize(Decimal("0.1"))
    stages = LoadRepository(session).stages(load.id)
    validated = next((s for s in stages if s.stage == "validated"), None)
    counts = dict(validated.detail.get("rules", {})) if validated else {}
    released = score >= threshold
    feed = FeedRepository(session).get(load.feed_id)
    warned: list[str] = []
    if not released:
        registration = FeedRegistrationRepository(session).current(load.feed_id)  # 7.14: the owner is told too
        warned = list(dict.fromkeys([*consumers_of(session, load.feed_id), *operations, *backend_team,
                                     *([registration.owner_id] if registration else [])]))
    LoadCertificationRepository(session).add(
        load_id=load.id, feed_id=load.feed_id, rows=rows, flagged_rows=flagged, rule_counts=counts,
        trust_score=score, threshold=threshold, outcome="released" if released else "held_consumers_warned",
        warned=warned, waived_rows=records.count_waived(load.id),  # 7.14: unflagged in the score, shown apart
        waived_counts=records.waived_counts(load.id))
    LoadRepository(session).set_status(load.id, "released" if released else "held")
    shared = dict(load_id=load.id, trust_score=score, threshold=threshold, rows=rows, flagged_rows=flagged)
    if released:
        return Released(**shared)
    from core.steps.exceptions import load_exceptions  # 7.14: the open groups a person can resolve

    groups = load_exceptions(session, load.id, limit=1).total_groups
    for person in warned:
        notifier.notify(recipient=person, subject=f"Load held: {feed.code}",
                        body=f"The scheduled load of {feed.code} scored {score} (threshold {threshold}): "
                             f"{flagged} of {rows} rows were flagged by a rule, in {groups} exception groups. It was "
                             "held, not released; the data released earlier stays available.",
                        link=f"#/feed/{feed.id}")  # phase 7.5, P28
    return HeldConsumersWarned(**shared, warned=warned)
