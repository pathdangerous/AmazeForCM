"""Phase 7.16, T0b: 21 days of history for the demo (plan docs/plans/phase-7.16.md, 2.2).

`python -m core.seed --demo-history` runs the real daily pipeline (DailyFetch.fetch_and_load: the fetch, step 12, the
engine, steps 15 and 20) once for each of the 21 days before today, oldest first, for a feed a person has already
onboarded and released. Two things are injected: a clock (the load's received_at is that day, so reports and patterns see a
past day) and a connector (it hands over that day's file). The seed writes no row of its own: every row is made by the steps
a delivery goes through, so every guard of the database applies.

A day's file is the feed's latest fetched file with the checked column made non-negative and its last number moved by the
day's number (so no two days are the same bytes: a resend would be quarantined). On a Monday the first `bad_rows` values of
the column are `bad_value`, so the feed's rule flags them: the same rule failing on every Monday gives one weekday pattern.
A day that already has a scheduled load is skipped, so running it again changes nothing.
"""

from __future__ import annotations

import csv
import io
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel

from apps.api.jobs import DailyFetch
from core.repositories.configuration import RuleRepository
from core.repositories.errors import FeedNotFound
from core.repositories.feeds import FeedRepository
from core.repositories.go_lives import GoLiveRepository
from core.repositories.loads import LoadRepository
from core.repositories.source_fetches import SourceFetchRepository
from ports.connectors.base import FetchedFile, SourceUnavailable

Reason = Literal["feed_missing", "not_live", "no_file", "column_missing", "too_few_rows"]


class HistorySpec(BaseModel):
    """What the demo history is made of. The defaults are the demo's security master."""

    feed_code: str = "syn_security_master"
    column: str = "BEST_SALES"  # the column the failing rule checks
    rule_key: str = "best_sales_not_negative"
    bad_value: str = "-1"  # what a Monday's first bad_rows values become: the rule flags them
    bad_rows: int = 8
    # the column that holds the day the file is for, if the feed has one: each day's file says its own day, so the provider
    # date differs from load to load and the failing Mondays make no provider-date pattern, only the weekday one
    date_column: str | None = "RECORD_DATE"
    date_format: str = "%d-%m-%Y"  # as the file writes it (the security master: 31-12-2019)
    days: int = 21
    hour: int = 9  # UTC, when each day's load is received
    minute: int = 0
    # Phase 7.21: days (counted back from today) on which the source did not answer, so no load was made (a missing delivery),
    # and days on which the file came late (at late_hour:late_minute instead of hour:minute). Both through the same pipeline.
    missing_days_ago: tuple[int, ...] = ()
    late_days_ago: tuple[int, ...] = ()
    late_hour: int = 10
    late_minute: int = 30


class HistorySeeded(BaseModel):
    outcome: Literal["history_seeded"] = "history_seeded"
    loaded: int
    skipped: int  # days that already had a scheduled load
    held: int  # of the loaded ones: below the trust threshold
    rule_found: bool  # False: the feed has no rule of that key, so nothing flags and there is no pattern
    problems: list[str]  # a day whose fetch or load did not end loaded, with what happened
    missing: int = 0  # phase 7.21: days the source did not answer (no load: a missing delivery)
    late: int = 0  # phase 7.21: days the file came late


class HistoryRefused(BaseModel):
    outcome: Literal["history_refused"] = "history_refused"
    reason: Reason
    detail: str


class _DayFile:
    """The connector of one day: it returns that day's file, whatever is asked."""

    name = "demo_history"

    def __init__(self, filename: str, content: bytes):
        self._file = FetchedFile(source_ref=filename, filename=filename, content=content, content_type="text/csv")

    def fetch(self, source_ref: str) -> FetchedFile:
        return self._file

    def close(self) -> None:
        pass

    def __enter__(self) -> _DayFile:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass


class _SourceDown:
    """The connector of a day the source did not answer."""

    name = "demo_history_down"

    def fetch(self, source_ref: str) -> FetchedFile:
        raise SourceUnavailable(source_ref)

    def close(self) -> None:
        pass

    def __enter__(self) -> _SourceDown:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass


def demo_spec(today: date | None = None) -> HistorySpec:
    """The demo's history (docs/demo.md): files come at 07:20 (the demo schedule is business days, 07:00, 60 minutes grace),
    one weekday from Tuesday to Thursday nothing comes, and two other weekdays the file comes at 10:30. Yesterday is never one
    of them, so yesterday's report still has its load; a Monday is never the missing one (the failing Mondays are the weekday
    pattern), nor a Friday (a Saturday file would then be taken for Friday's). Weekend files are "unexpected" deliveries."""
    today = today or date.today()
    weekdays = [n for n in range(2, 22) if (today - timedelta(days=n)).weekday() < 5]
    missing = next(n for n in weekdays[4:] if (today - timedelta(days=n)).weekday() in (1, 2, 3))
    candidates = [n for n in weekdays if n != missing and (today - timedelta(days=n)).weekday() != 0]  # a late Monday is "held"
    late = (candidates[1], candidates[7])
    return HistorySpec(hour=7, minute=20, missing_days_ago=(missing,), late_days_ago=late)


def _number(cell: str) -> int | float | None:
    for kind in (int, float):
        try:
            return kind(cell)
        except ValueError:
            continue
    return None


def _day_file(header: list[str], rows: list[list[str]], spec: HistorySpec, day: date, number: int) -> bytes:
    """The base rows with the column non-negative, the last number moved by `number`, and on a Monday the first bad rows bad."""
    at = header.index(spec.column)
    out = [list(row) for row in rows]
    for row in out:
        value = _number(row[at])
        if value is not None:
            row[at] = str(abs(value))
    last = max(i for i, row in enumerate(out) if i >= spec.bad_rows and _number(row[at]) is not None)
    row_value = _number(out[last][at])
    assert row_value is not None
    out[last][at] = str(row_value + number)
    if day.weekday() == 0:
        for row in out[:spec.bad_rows]:
            row[at] = spec.bad_value
    if spec.date_column is not None:
        for row in out:
            row[header.index(spec.date_column)] = day.strftime(spec.date_format)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(out)
    return buffer.getvalue().encode("utf-8")


def seed_demo_history(daily: DailyFetch, *, today: date | None = None, spec: HistorySpec | None = None
                      ) -> HistorySeeded | HistoryRefused:
    spec = spec or HistorySpec()
    today = today or date.today()
    with daily.session_factory() as session:
        try:
            feed = FeedRepository(session).get_by_code(spec.feed_code)
        except FeedNotFound:
            session.rollback()
            return HistoryRefused(reason="feed_missing", detail=f"There is no feed {spec.feed_code!r}: onboard it first.")
        fetch = SourceFetchRepository(session).latest_fetched(feed.id)
        if GoLiveRepository(session).latest(feed.id) is None:
            session.rollback()
            return HistoryRefused(reason="not_live", detail=f"{feed.code} has no go-live: approve it and load once first.")
        if fetch is None:
            session.rollback()
            return HistoryRefused(reason="no_file", detail=f"{feed.code} has no fetched file to make the days from.")
        feed_id = feed.id
        rule_found = any(r.rule_key == spec.rule_key for r in RuleRepository(session).current_for_feed(feed_id))
        have = {load.received_at.astimezone(UTC).date() for load in LoadRepository(session).list_for_feed(feed_id)
                if load.purpose == "scheduled"}
        blob_ref = fetch.blob_ref
        session.rollback()  # the file is read with no connection held
    table = list(csv.reader(io.StringIO(daily.blob_store.get(blob_ref).decode("utf-8-sig"))))
    header, rows = table[0], [row for row in table[1:] if row]
    missing = [c for c in (spec.column, spec.date_column) if c is not None and c not in header]
    if missing:
        return HistoryRefused(reason="column_missing", detail=f"The file has no column {missing[0]!r}.")
    if len(rows) <= spec.bad_rows or not any(_number(r[header.index(spec.column)]) is not None for r in rows[spec.bad_rows:]):
        return HistoryRefused(reason="too_few_rows", detail=f"The file needs more than {spec.bad_rows} rows, and a number in "
                                                            f"{spec.column} after them.")
    loaded = skipped = held = missing = late = 0
    problems: list[str] = []
    for number, day in enumerate((today - timedelta(days=n) for n in range(spec.days, 0, -1)), start=1):
        if day in have:
            skipped += 1
            continue
        ago = (today - day).days
        if ago in spec.missing_days_ago:  # the source does not answer: the fetch says so, and no load is made
            down = _SourceDown()
            done = replace(daily, connector_factory=lambda name, down=down: down).fetch_and_load(feed_id, today=day)
            if done["fetch"] == "source_down":
                missing += 1
            else:
                problems.append(f"{day}: fetch {done['fetch']}, load {done['load']}")
            continue
        at = time(spec.late_hour, spec.late_minute) if ago in spec.late_days_ago else time(spec.hour, spec.minute)
        file = _DayFile(f"history_{day.isoformat()}.csv", _day_file(header, rows, spec, day, number))
        job = replace(daily, connector_factory=lambda name, file=file: file,
                      clock=lambda day=day, at=at: datetime.combine(day, at, tzinfo=UTC))
        done = job.fetch_and_load(feed_id, today=day)
        if done["load"] in ("released", "held"):
            loaded += 1
            late += ago in spec.late_days_ago
            held += done["load"] == "held"
        else:
            problems.append(f"{day}: fetch {done['fetch']}, load {done['load']}")
    return HistorySeeded(loaded=loaded, skipped=skipped, held=held, rule_found=rule_found, problems=problems,
                         missing=missing, late=late)


def run() -> HistorySeeded | HistoryRefused:
    """The command line's entry: the real settings, identity and database."""
    from ports.config import Settings
    from ports.identity import get_identity

    settings = Settings()
    return seed_demo_history(DailyFetch.from_settings(settings, get_identity(settings)), spec=demo_spec())
