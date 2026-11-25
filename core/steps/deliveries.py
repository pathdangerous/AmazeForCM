"""Expected against actual deliveries of one feed (phase 7.21, T3; plan docs/plans/phase-7.21.md, assumptions 5 to 13).

Deterministic code: no model call, no clock of its own (`now` is a parameter), no write except the alert rows.

build_deliveries(versions, loads, start=, now=)   pure. The expected deliveries of every schedule version, each in force from
        its own moment (version 1 from the beginning), and the scheduled load that arrived for each.
deliveries_for_feed(session, feed_id, window=)     reads the feed, returns a typed result:
        deliveries_shown (a recurring feed), one_time_shown (its loads, labelled), schedule_not_set.
record_missing_alerts(session, feed_id=, ...)      a missing delivery tells the feed's owner once (delivery_alerts: one row per
        feed and due instant); the result is a step 12 `late_alert` for each.

Matching (assumption 8). Slots are tiled: a slot's span starts 12 hours before its due instant (or at the midpoint to the
previous one, if that is later) and ends at the midpoint to the next one, but no later than 24 hours after its grace ends. A load
whose `received_at` falls in a span is that slot's delivery; the first one claims it, another is `second_delivery_for_one_due`,
and one in no span is `no_delivery_was_due`. A delivery is `on_time` until due + grace; after that it is `late` by the minutes
past the end of the grace. No load by the end of the grace is `missing`; before it, `awaited`. The cell's one `status` is
missing, else resent_quarantined, else held, else the timing; the load's `result` (released, held, ...) is kept beside it.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.domain.schedule import FeedSchedule
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feeds import FeedRepository
from core.repositories.schedules import FeedScheduleRepository
from core.repositories.tables import DeliveryAlertRow, FeedGoLiveRow, LoadCertificationRow, LoadRow
from core.steps.result import StepResult
from core.steps.s12_arrival import LateAlert
from ports.notify.base import Notifier

Window = Literal["30", "90", "all"]
EARLY = timedelta(hours=12)  # a delivery this early is still the next one's
LATE_CAP = timedelta(hours=24)  # and one this long after its grace ends is no longer that slot's
ALERT_LOOKBACK = timedelta(days=7)  # a missing delivery older than this is shown, but the owner is not told now
Timing = Literal["on_time", "late", "missing", "awaited"]
DeliveryStatus = Literal["on_time", "late", "missing", "resent_quarantined", "held", "awaited"]
Result = Literal["released", "held", "quarantined", "failed", "loaded", "received", "abandoned"]
UnexpectedReason = Literal["no_delivery_was_due", "second_delivery_for_one_due"]
_RESULT: dict[str, Result] = {"released": "released", "held": "held", "quarantined": "quarantined",
                              "failed_rolled_back": "failed", "loaded": "loaded", "received": "received",
                              "abandoned": "abandoned"}


class Due(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: datetime  # UTC
    schedule_version: int
    grace_minutes: int
    notify_owner: bool = True


class LoadFact(BaseModel):
    """What the matching needs of one scheduled load."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    received_at: datetime
    status: str
    arrival_outcome: str | None = None
    rows: int | None = None  # the trust score's row count, once the load is certified


class DeliveryRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    due_at: datetime
    schedule_version: int
    grace_minutes: int
    status: DeliveryStatus
    timing: Timing
    result: Result | None = None
    load_id: UUID | None = None
    received_at: datetime | None = None
    late_minutes: int | None = None
    minutes_from_due: int | None = None  # signed: negative is early
    rows: int | None = None


class UnexpectedDelivery(BaseModel):
    model_config = ConfigDict(frozen=True)

    load_id: UUID
    received_at: datetime
    status: str
    reason: UnexpectedReason


class Figures(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected: int  # deliveries whose grace has ended (awaited ones are not counted)
    on_time: int
    late: int
    missing: int
    on_time_pct: float | None
    mean_late_minutes: float | None
    streak: int  # consecutive newest expected deliveries that were on time
    next_expected: datetime | None
    unexpected: int


class Deliveries(BaseModel):
    model_config = ConfigDict(frozen=True)

    rows: list[DeliveryRow]
    unexpected: list[UnexpectedDelivery]
    figures: Figures


# ---------------------------------------------------------------- expected instants


def _matches(v: FeedSchedule, day: date) -> bool:
    if v.frequency == "daily":
        return True
    if v.frequency == "business_days":
        return day.weekday() < 5
    if v.frequency == "weekly":
        return day.weekday() == v.weekday
    if v.frequency == "monthly":
        last = calendar.monthrange(day.year, day.month)[1]
        return day.day == (last if v.day_of_month == "last" else int(v.day_of_month or 0))
    return False


def due_instants(versions: Sequence[FeedSchedule], start: datetime, end: datetime) -> list[Due]:
    """The due instants in [start, end), each by the schedule version in force at that instant: version 1 from the
    beginning, each later one from its `created_at`, to the next one's. A one-time version expects nothing."""
    out: list[Due] = []
    for position, v in enumerate(versions):
        if v.kind != "recurring" or v.expected_time is None:
            continue
        in_force_from = datetime.min.replace(tzinfo=UTC) if position == 0 else v.created_at
        in_force_to = versions[position + 1].created_at if position + 1 < len(versions) else datetime.max.replace(tzinfo=UTC)
        zone = ZoneInfo(v.timezone)
        day = (start - timedelta(days=2)).astimezone(zone).date()
        last_day = (end + timedelta(days=2)).astimezone(zone).date()
        while day <= last_day:
            if _matches(v, day):
                at = datetime.combine(day, v.expected_time, tzinfo=zone).astimezone(UTC)
                if start <= at < end and in_force_from <= at < in_force_to:
                    out.append(Due(at=at, schedule_version=v.version, grace_minutes=v.grace_minutes,
                                   notify_owner=v.notify_owner))
            day += timedelta(days=1)
    return sorted(out, key=lambda d: d.at)


# ---------------------------------------------------------------- matching


def _spans(dues: Sequence[Due]) -> list[tuple[datetime, datetime]]:
    spans = []
    for i, due in enumerate(dues):
        grace = timedelta(minutes=due.grace_minutes)
        low = due.at - EARLY
        if i > 0:
            low = max(low, dues[i - 1].at + (due.at - dues[i - 1].at) / 2)
        high = due.at + grace + LATE_CAP
        if i + 1 < len(dues):
            high = min(high, due.at + (dues[i + 1].at - due.at) / 2)
        spans.append((low, high))
    return spans


def build_deliveries(versions: Sequence[FeedSchedule], loads: Sequence[LoadFact], *, start: datetime,
                     now: datetime) -> Deliveries:
    """The expected deliveries from `start` to `now` and the load that arrived for each. Pure and deterministic."""
    dues = due_instants(versions, start - timedelta(days=40), now + timedelta(days=62))
    spans = _spans(dues)
    claimed: dict[int, LoadFact] = {}
    unexpected: list[UnexpectedDelivery] = []
    for load in sorted((x for x in loads if x.received_at >= start), key=lambda x: (x.received_at, str(x.id))):
        slot = next((i for i, (low, high) in enumerate(spans) if low <= load.received_at < high), None)
        if slot is None:
            unexpected.append(UnexpectedDelivery(load_id=load.id, received_at=load.received_at, status=load.status,
                                                 reason="no_delivery_was_due"))
        elif slot in claimed:
            unexpected.append(UnexpectedDelivery(load_id=load.id, received_at=load.received_at, status=load.status,
                                                 reason="second_delivery_for_one_due"))
        else:
            claimed[slot] = load
    rows: list[DeliveryRow] = []
    for i, due in enumerate(dues):
        if due.at < start or due.at > now:
            continue
        end_of_grace = due.at + timedelta(minutes=due.grace_minutes)
        load = claimed.get(i)
        if load is None:
            timing: Timing = "awaited" if now <= end_of_grace else "missing"
            rows.append(DeliveryRow(due_at=due.at, schedule_version=due.schedule_version, grace_minutes=due.grace_minutes,
                                    status=timing, timing=timing))
            continue
        late = load.received_at > end_of_grace
        timing = "late" if late else "on_time"
        status: DeliveryStatus = ("resent_quarantined" if load.status == "quarantined" else "held" if load.status == "held"
                                  else timing)
        rows.append(DeliveryRow(
            due_at=due.at, schedule_version=due.schedule_version, grace_minutes=due.grace_minutes, status=status,
            timing=timing, result=_RESULT.get(load.status), load_id=load.id, received_at=load.received_at,
            late_minutes=int((load.received_at - end_of_grace).total_seconds() // 60) if late else None,
            minutes_from_due=round((load.received_at - due.at).total_seconds() / 60), rows=load.rows))
    next_due = next((d for k, d in enumerate(dues) if k not in claimed
                     and d.at + timedelta(minutes=d.grace_minutes) > now), None)
    return Deliveries(rows=rows, unexpected=unexpected, figures=_figures(rows, len(unexpected),
                                                                          next_due.at if next_due else None))


def _figures(rows: Sequence[DeliveryRow], unexpected: int, next_expected: datetime | None) -> Figures:
    counted = [r for r in rows if r.timing != "awaited"]
    on_time = sum(r.timing == "on_time" for r in counted)
    lates = [r.late_minutes or 0 for r in counted if r.timing == "late"]
    streak = 0
    for r in reversed(counted):
        if r.timing != "on_time":
            break
        streak += 1
    return Figures(expected=len(counted), on_time=on_time, late=len(lates), missing=sum(r.timing == "missing" for r in counted),
                   on_time_pct=round(100 * on_time / len(counted), 1) if counted else None,
                   mean_late_minutes=round(sum(lates) / len(lates), 1) if lates else None, streak=streak,
                   next_expected=next_expected, unexpected=unexpected)


# ---------------------------------------------------------------- the typed result for one feed


class DeliveriesShown(StepResult):
    outcome: Literal["deliveries_shown"] = "deliveries_shown"
    feed_id: UUID
    window: Window
    schedule: FeedSchedule  # the version in force now
    live_since: datetime | None  # None: the feed has not loaded yet, so nothing is expected
    now: datetime
    start: datetime
    rows: list[DeliveryRow]
    unexpected: list[UnexpectedDelivery]
    figures: Figures


class OneTimeLoad(BaseModel):
    model_config = ConfigDict(frozen=True)

    load_id: UUID
    received_at: datetime
    status: str
    purpose: str
    rows: int | None


class OneTimeShown(StepResult):
    outcome: Literal["one_time_shown"] = "one_time_shown"
    feed_id: UUID
    label: Literal["One-time load"] = "One-time load"
    schedule: FeedSchedule
    loads: list[OneTimeLoad]


class ScheduleNotSet(StepResult):
    outcome: Literal["schedule_not_set"] = "schedule_not_set"
    feed_id: UUID


DeliveriesResult = Annotated[DeliveriesShown | OneTimeShown | ScheduleNotSet, Field(discriminator="outcome")]


def _facts(session: Session, feed_id: UUID, *, purposes: Sequence[str]) -> list[tuple[LoadRow, int | None]]:
    query = (select(LoadRow, LoadCertificationRow.rows)
             .outerjoin(LoadCertificationRow, LoadCertificationRow.load_id == LoadRow.id)
             .where(LoadRow.feed_id == feed_id, LoadRow.purpose.in_(purposes)).order_by(LoadRow.received_at, LoadRow.id))
    return [(load, rows) for load, rows in session.execute(query)]


def live_since(session: Session, feed_id: UUID) -> tuple[datetime, datetime] | None:
    """(when the feed became live, when its deliveries are first expected), or None when it has neither a go-live nor a
    scheduled load. Became live: the earlier of the first go-live and the first scheduled load (a history seed receives
    loads before the go-live row is made). Expected from: the go-live, until a scheduled load shows the feed loaded earlier,
    and then 12 hours before that load (so the delivery it was for is not left out). Nothing is expected from before a feed
    could load."""
    first_live = session.scalar(select(func.min(FeedGoLiveRow.created_at)).where(FeedGoLiveRow.feed_id == feed_id))
    first_load = session.scalar(select(func.min(LoadRow.received_at)).where(LoadRow.feed_id == feed_id,
                                                                            LoadRow.purpose == "scheduled"))
    found = [t for t in (first_live, first_load) if t is not None]
    if not found:
        return None
    expected_from = min(t for t in (first_live, None if first_load is None else first_load - EARLY) if t is not None)
    return min(found), expected_from


def deliveries_for_feed(session: Session, feed_id: UUID, *, window: Window = "30",
                        now: datetime | None = None) -> DeliveriesResult:
    """Read only. FeedNotFound for an unknown feed."""
    now = now or datetime.now(UTC)
    FeedRepository(session).get(feed_id)
    versions = FeedScheduleRepository(session).list_for_feed(feed_id)
    if not versions:
        return ScheduleNotSet(feed_id=feed_id)
    current = versions[-1]
    if current.kind == "one_time":
        loads = _facts(session, feed_id, purposes=("scheduled", "go_live_test"))
        return OneTimeShown(feed_id=feed_id, schedule=current, loads=[
            OneTimeLoad(load_id=load.id, received_at=load.received_at, status=load.status, purpose=load.purpose, rows=rows)
            for load, rows in loads])
    live = live_since(session, feed_id)
    if live is None:
        return DeliveriesShown(feed_id=feed_id, window=window, schedule=current, live_since=None, now=now, start=now,
                               rows=[], unexpected=[], figures=_figures([], 0, None))
    since, start = live
    if window != "all":
        start = max(start, now - timedelta(days=int(window)))
    facts = [LoadFact(id=load.id, received_at=load.received_at, status=load.status, arrival_outcome=load.arrival_outcome,
                      rows=rows) for load, rows in _facts(session, feed_id, purposes=("scheduled",))]
    built = build_deliveries(versions, facts, start=start, now=now)
    return DeliveriesShown(feed_id=feed_id, window=window, schedule=current, live_since=since, now=now, start=start,
                           rows=built.rows, unexpected=built.unexpected, figures=built.figures)


# ---------------------------------------------------------------- the operations overview


class FeedDeliveryRank(BaseModel):
    model_config = ConfigDict(frozen=True)

    feed_id: UUID
    code: str
    name: str
    schedule: str  # one line, as the feed page writes it
    expected: int
    on_time: int
    late: int
    missing: int
    on_time_pct: float | None
    streak: int


class DeliveriesOverview(StepResult):
    outcome: Literal["deliveries_overview"] = "deliveries_overview"
    window: Literal["30"] = "30"
    feeds: list[FeedDeliveryRank]  # recurring feeds, worst on-time % first; a feed with nothing due yet is last
    one_time: int  # feeds that are one-time loads (never late or missing)
    not_set: int  # live feeds with no schedule yet


def deliveries_overview(session: Session, *, now: datetime | None = None) -> DeliveriesOverview:
    """Read only: every registered or paused feed, over the last 30 days."""
    now = now or datetime.now(UTC)
    ranked: list[FeedDeliveryRank] = []
    one_time = not_set = 0
    feeds = FeedRepository(session)
    for feed in (*feeds.list("registered"), *feeds.list("paused")):
        shown = deliveries_for_feed(session, feed.id, window="30", now=now)
        if isinstance(shown, ScheduleNotSet):
            not_set += 1
        elif isinstance(shown, OneTimeShown):
            one_time += 1
        else:
            f = shown.figures
            ranked.append(FeedDeliveryRank(feed_id=feed.id, code=feed.code, name=feed.name,
                                           schedule=shown.schedule.describe(), expected=f.expected, on_time=f.on_time,
                                           late=f.late, missing=f.missing, on_time_pct=f.on_time_pct, streak=f.streak))
    ranked.sort(key=lambda r: (r.on_time_pct is None, r.on_time_pct if r.on_time_pct is not None else 0.0, -r.missing, r.code))
    return DeliveriesOverview(feeds=ranked, one_time=one_time, not_set=not_set)


# ---------------------------------------------------------------- the missing alert


def record_missing_alerts(session: Session, *, feed_id: UUID, notifier: Notifier,
                          now: datetime | None = None) -> list[LateAlert]:
    """Tell the feed's owner, once for each, of the missing deliveries of the last 7 days whose schedule had notify_owner on.
    A `delivery_alerts` row (feed, due instant) is the once: it is written before the owner is told, in the caller's
    transaction, and the unique key refuses a second. The feed row is locked first. Nothing for a one-time feed."""
    now = now or datetime.now(UTC)
    feed = FeedRepository(session).lock(feed_id)
    shown = deliveries_for_feed(session, feed_id, window="30", now=now)
    if not isinstance(shown, DeliveriesShown):
        return []
    versions = {v.version: v for v in FeedScheduleRepository(session).list_for_feed(feed_id)}
    done = set(session.scalars(select(DeliveryAlertRow.due_at).where(DeliveryAlertRow.feed_id == feed_id)))
    registration = FeedRegistrationRepository(session).current(feed_id)
    owner = registration.owner_id if registration else feed.owner_id
    sent: list[LateAlert] = []
    for row in shown.rows:
        schedule = versions[row.schedule_version]
        if (row.timing != "missing" or not schedule.notify_owner or row.due_at in done
                or row.due_at + timedelta(minutes=row.grace_minutes) < now - ALERT_LOOKBACK):
            continue
        session.add(DeliveryAlertRow(feed_id=feed_id, due_at=row.due_at, schedule_version=row.schedule_version))
        session.flush()
        detail = (f"The delivery expected at {row.due_at:%Y-%m-%d %H:%M} UTC did not arrive "
                  f"({row.grace_minutes} minutes grace have ended).")
        if owner:
            notifier.notify(recipient=owner, subject=f"Delivery missing: {feed.code}",
                            body=f"{detail} The feed's last released data stays available.", link=f"#/feed/{feed_id}")
        sent.append(LateAlert(feed_id=feed_id, detail=detail))
    return sent
