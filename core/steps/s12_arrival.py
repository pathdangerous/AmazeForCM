"""Step 12, arrival and structure (phase 7 plan, assumptions 5 to 17; sections 5.1 to 5.3).

Outcomes (CLAUDE.md): on_time_complete, late_alert, resent_quarantined, additive_parked, breaking_held. Rules
execute: no agent, no model call. Step 12 runs on every scheduled load, between the fetch and the engine; a
go_live_test load skips it.

Three parts, as for the fetch and the profile, so no database connection is held while the file is read:
    arrival_plan(session, load_id=..)   reads: the load (received, scheduled), the feed, the template (the feed's
                                        latest profile: assumption 10) and the SHA-256 of every earlier scheduled
                                        load. The caller then ends the transaction.
    check_arrival(plan, blob_store=..)  no session: BlobStore.get(), the SHA-256, DuckDB in memory. First match
                                        wins: resent_quarantined (same bytes as an earlier scheduled load),
                                        breaking_held (a template column is missing, the file is not a CSV table
                                        or is missing from the store), additive_parked (columns the template
                                        lacks), on_time_complete. Column names are compared in any case, as the
                                        engine matches them; order does not matter (assumption 9); types are not
                                        compared (assumption 11).
    record_arrival(session, check, ..)  locks the feed row, then (additive) writes the load's own profile and
                                        queues a glossary run, then locks the load row, checks it is still
                                        received and writes the arrival, in one savepoint; then tells people.

A file with no data rows passes (assumption 15: step 13 fails it), so it is never parked: a profile needs rows.
The step flushes; the caller commits. late_alert is the daily job's (a feed with a go-live whose fetch is down).
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.load import ArrivalOutcome, Load
from core.domain.profiling import FileProfile, SourceSpec
from core.repositories.errors import FeedHasNoFile, FeedNotProfiled, LoadNotReceived, NotAScheduledLoad
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feeds import FeedRepository
from core.repositories.loads import LoadRepository
from core.repositories.products import ProductRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.profiling import NotATable, profile_csv
from core.steps.result import StepResult
from core.steps.s05_glossary import queue_run
from ports.notify.base import Notifier
from ports.storage.base import BlobNotFound, BlobStore

PROFILED_BY = "step_12"
CONSUMER_STATUSES = ("onboarding", "ready", "access_restricted")  # a requirement that still waits on, or uses, a feed


class OnTimeComplete(StepResult):
    outcome: Literal["on_time_complete"] = "on_time_complete"
    load_id: UUID


class LateAlert(StepResult):
    outcome: Literal["late_alert"] = "late_alert"
    feed_id: UUID
    detail: str  # the fetch's own words: the source did not answer


class ResentQuarantined(StepResult):
    outcome: Literal["resent_quarantined"] = "resent_quarantined"
    load_id: UUID
    same_as: UUID  # the oldest earlier scheduled load with the same bytes


class AdditiveParked(StepResult):
    outcome: Literal["additive_parked"] = "additive_parked"
    load_id: UUID
    added: list[str]
    profile_id: UUID
    run_id: UUID


class BreakingHeld(StepResult):
    outcome: Literal["breaking_held"] = "breaking_held"
    load_id: UUID
    missing: list[str]  # template columns the file lacks; empty when the file is not a table
    reason: str


ArrivalResult = Annotated[OnTimeComplete | LateAlert | ResentQuarantined | AdditiveParked | BreakingHeld,
                          Field(discriminator="outcome")]


class Recipients(BaseModel):
    """Who step 12 tells (assumption 14); the feed's owner and the consumers it reads from the database."""

    model_config = ConfigDict(frozen=True)

    glossary_owners: Sequence[str] = ()
    stewards: Sequence[str] = ()
    backend_team: Sequence[str] = ()
    operations: Sequence[str] = ()


class ArrivalPlan(BaseModel):
    """What one check needs, read in a transaction the caller then ends."""

    model_config = ConfigDict(frozen=True)

    load_id: UUID
    feed_id: UUID
    feed_code: str
    file_ref: str
    fetch_id: UUID  # the file's fetch: the load's own, else the feed's latest (a profile names one)
    template: list[str]  # the column names of the feed's latest profile
    template_spec: SourceSpec
    earlier: dict[str, UUID]  # SHA-256 -> the oldest scheduled load that had it


class ArrivalCheck(BaseModel):
    """What the file showed. kind is the outcome; the rest is its evidence."""

    model_config = ConfigDict(frozen=True)

    plan: ArrivalPlan
    kind: ArrivalOutcome
    sha256: str | None  # None: the file is missing from the store
    columns: list[str] = []  # the file's header
    same_as: UUID | None = None
    missing: list[str] = []
    added: list[str] = []
    reason: str | None = None
    file: FileProfile | None = None  # additive_parked: the load's own profile


# ---------------------------------------------------------------- late_alert (the daily job's)


def record_late_alert(session: Session, *, feed_id: UUID, detail: str, notifier: Notifier,
                      operations: Sequence[str]) -> LateAlert:
    """The daily fetch of a feed with a go-live found its source down: no load row is made; operations and the
    feed's owner are told. There is no delivery deadline in the PoC (assumption 8): late is 'did not arrive today'."""
    feed = FeedRepository(session).get(feed_id)
    registration = FeedRegistrationRepository(session).current(feed_id)
    _tell(notifier, [*operations, *([registration.owner_id] if registration else [])], f"Delivery late: {feed.code}",
          f"No delivery of {feed.code} arrived today: {detail} The feed's last released data stays available.")
    return LateAlert(feed_id=feed_id, detail=detail)


# ---------------------------------------------------------------- a load left received (assumption 18)


def clear_stale_load(session: Session, *, feed_id: UUID, stale_minutes: int, notifier: Notifier,
                     operations: Sequence[str], now: datetime | None = None) -> Load | None:
    """Before a load is created: the feed's received load older than `stale_minutes` was left by a process that
    died (a load commits its stages, records and status together, so it has none) and becomes abandoned;
    operations are told. A younger one stays (the new load is LoadInProgress). Lock order: the feed row, then the
    load row, checked still received. Returns the abandoned load, or None."""
    feed = FeedRepository(session).lock(feed_id)
    loads = LoadRepository(session)
    current = loads.received_for_feed(feed_id)
    if current is None or current.received_at > (now or datetime.now(UTC)) - timedelta(minutes=stale_minutes):
        return None
    if loads.lock(current.id).status != "received":
        return None
    abandoned = loads.abandon(current.id, f"Left received for more than {stale_minutes} minutes by a process that "
                                          "did not finish; abandoned before the next load.")
    _tell(notifier, operations, f"Load abandoned: {feed.code}",
          f"The {current.purpose} load of {feed.code} received at {current.received_at:%Y-%m-%d %H:%M} UTC was "
          f"still received after {stale_minutes} minutes, so it was abandoned and the feed may load again.")
    return abandoned


# ---------------------------------------------------------------- plan


def arrival_plan(session: Session, *, load_id: UUID) -> ArrivalPlan:
    """Read only. LoadNotFound, LoadNotReceived, NotAScheduledLoad, FeedNotProfiled, FeedHasNoFile."""
    loads = LoadRepository(session)
    load = loads.get(load_id)
    if load.status != "received":
        raise LoadNotReceived(f"Load {load_id} is {load.status}, not received")
    if load.purpose != "scheduled":
        raise NotAScheduledLoad(f"Load {load_id} is a {load.purpose} load; only a scheduled load has an arrival")
    feed = FeedRepository(session).get(load.feed_id)
    template = FeedProfileRepository(session).latest(feed.id)
    if template is None:
        raise FeedNotProfiled(f"Feed {feed.code!r} has no profile to compare a delivery with")
    fetch_id = load.fetch_id
    if fetch_id is None:
        fetch = SourceFetchRepository(session).latest_fetched(feed.id)
        if fetch is None:
            raise FeedHasNoFile(f"Feed {feed.code!r} has no fetched file")
        fetch_id = fetch.id
    return ArrivalPlan(load_id=load.id, feed_id=feed.id, feed_code=feed.code, file_ref=load.file_ref,
                       fetch_id=fetch_id, template=[c.name for c in template.columns], template_spec=template.spec,
                       earlier=loads.scheduled_hashes(feed.id))


# ---------------------------------------------------------------- check (no session)


def check_arrival(plan: ArrivalPlan, *, blob_store: BlobStore) -> ArrivalCheck:
    """No database. BlobStore.get(), a temporary file, DuckDB in memory. Never raises for an expected case."""
    try:
        content = blob_store.get(plan.file_ref)
    except BlobNotFound:
        return ArrivalCheck(plan=plan, kind="breaking_held", sha256=None,
                            reason=f"The file is missing from the store: {plan.file_ref}")
    sha = hashlib.sha256(content).hexdigest()
    if sha in plan.earlier:
        return ArrivalCheck(plan=plan, kind="resent_quarantined", sha256=sha, same_as=plan.earlier[sha],
                            reason="The file is byte for byte the same as an earlier delivery.")
    with tempfile.TemporaryDirectory(prefix="amaze-arrival-") as folder:
        path = Path(folder) / "file.csv"
        path.write_bytes(content)
        try:
            file = profile_csv(path)
        except NotATable as exc:
            return ArrivalCheck(plan=plan, kind="breaking_held", sha256=sha,
                                reason=f"DuckDB could not read the file as a CSV table: {exc}")
    header = [c.name for c in file.columns]
    have = {name.lower() for name in header}
    known = {name.lower() for name in plan.template}
    missing = [name for name in plan.template if name.lower() not in have]
    added = [name for name in header if name.lower() not in known] if plan.template else []
    if missing:
        return ArrivalCheck(plan=plan, kind="breaking_held", sha256=sha, columns=header, missing=missing,
                            reason=f"{len(missing)} column(s) of the last accepted structure are not in the "
                                   f"file: {', '.join(missing)}")
    if added and file.row_count > 0:
        return ArrivalCheck(plan=plan, kind="additive_parked", sha256=sha, columns=header, added=added, file=file,
                            reason=f"{len(added)} new column(s): {', '.join(added)}")
    return ArrivalCheck(plan=plan, kind="on_time_complete", sha256=sha, columns=header, added=added)


# ---------------------------------------------------------------- record


def consumers_of(session: Session, feed_id: UUID) -> list[str]:
    """Assumption 23: the requesters of every requirement whose current choice selects the feed (onboarding, ready
    or access_restricted), and the product's owner."""
    requirements = RequirementRepository(session)
    people = [r.requester_id for r in (requirements.get(i) for i in SourceChoiceRepository(session)
                                       .requirements_selecting(feed_id)) if r.status in CONSUMER_STATUSES]
    product = ProductRepository(session).for_feed(feed_id)
    if product is not None:
        people.append(product.owner_id)
    return list(dict.fromkeys(people))


def record_arrival(session: Session, check: ArrivalCheck, *, notifier: Notifier,
                   recipients: Recipients) -> OnTimeComplete | ResentQuarantined | AdditiveParked | BreakingHeld:
    """Lock the feed row, write (one savepoint: the profile and the run of an additive change, then the load row
    locked last and checked still received), tell people. LoadNotReceived: nothing is written."""
    plan = check.plan
    feed = FeedRepository(session).lock(plan.feed_id)
    loads = LoadRepository(session)
    profile_id = run = None
    created = True
    with session.begin_nested():
        if check.kind == "additive_parked":
            profile = FeedProfileRepository(session).add_version(
                feed_id=feed.id, fetch_id=plan.fetch_id, outcome="profiled", row_count=check.file.row_count,
                columns=check.file.columns, coverage=[], spec=plan.template_spec, engine=check.file.engine,
                profiled_by=PROFILED_BY)
            profile_id = profile.id
            run, created = queue_run(session, feed_id=feed.id, reason="structure_change", requested_by=PROFILED_BY,
                                     columns=check.added)
        if loads.lock(plan.load_id).status != "received":
            raise LoadNotReceived(f"Load {plan.load_id} was finished meanwhile; nothing is written")
        detail = _detail(check, run_id=run.id if run else None, run_reused=not created)
        loads.record_arrival(plan.load_id, outcome=check.kind, content_sha256=check.sha256, detail=detail,
                             profile_id=profile_id)  # no SHA-256 for a file missing from the store
    return _result(session, check, feed.code, profile_id, run, notifier, recipients)


def _detail(check: ArrivalCheck, *, run_id: UUID | None, run_reused: bool) -> dict:
    detail: dict = {"columns": check.columns, "reason": check.reason}
    if check.kind == "resent_quarantined":
        detail["same_as"] = str(check.same_as)
    if check.kind == "breaking_held":
        detail["missing"] = check.missing
    if check.added:
        detail["added"] = check.added
        if check.kind == "additive_parked":
            detail.update(run_id=str(run_id), run_reused=run_reused)
        else:  # a file with no data rows is never parked (assumption 15)
            detail["not_parked"] = "The file has no data rows, so the new columns were not profiled."
    return detail


def _result(session: Session, check: ArrivalCheck, code: str, profile_id: UUID | None, run, notifier: Notifier,
            recipients: Recipients):
    plan = check.plan
    registration = FeedRegistrationRepository(session).current(plan.feed_id)
    owner = [registration.owner_id] if registration else []
    if check.kind == "resent_quarantined":
        _tell(notifier, [*recipients.operations, *owner], f"Delivery resent: {code}",
              f"The delivery of {code} is byte for byte the same as an earlier one, so it was set aside and not "
              f"loaded. The earlier delivery was load {check.same_as}.")
        return ResentQuarantined(load_id=plan.load_id, same_as=check.same_as)
    if check.kind == "breaking_held":
        _tell(notifier, [*recipients.backend_team, *recipients.operations, *owner, *consumers_of(session, plan.feed_id)],
              f"Delivery held: {code}", f"The delivery of {code} was held and not loaded. {check.reason} "
                                        "The data released earlier stays available.")
        return BreakingHeld(load_id=plan.load_id, missing=check.missing, reason=check.reason)
    if check.kind == "additive_parked":
        _tell(notifier, [*recipients.glossary_owners, *recipients.stewards, *recipients.backend_team],
              f"New column in {code}: {', '.join(check.added)}",
              f"The delivery of {code} has {len(check.added)} column(s) the feed did not have: "
              f"{', '.join(check.added)}. They are parked, not loaded; the glossary builder was asked about them. "
              "They load only once mapped and a new go-live is approved.")
        return AdditiveParked(load_id=plan.load_id, added=check.added, profile_id=profile_id, run_id=run.id)
    return OnTimeComplete(load_id=plan.load_id)


def _tell(notifier: Notifier, people: Sequence[str], subject: str, body: str) -> None:
    for person in dict.fromkeys(p for p in people if p):
        notifier.notify(recipient=person, subject=subject, body=body)
