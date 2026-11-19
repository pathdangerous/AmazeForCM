"""S4 fetch: read a feed's source, store the file, and hand it to step 02 (phase 3 plan, assumptions 10
to 15; changes 2 and 3).

Outcomes (CLAUDE.md): fetched; source_down. Rules execute: no model call, no decision by a person.

- The connector is built for this one fetch and always closed.
- fetched: the file is stored under raw/<feed>/<date>/<fetch id>/<file>; the fetch id is the
  source_fetches row's id, made first, so two fetches on one day keep both files. Every requirement
  handed to the feed that is still source_selected moves to onboarding.
- source_down: the connector could not read the source, or the file is empty. The reason is recorded;
  the requirement waits, and the daily job tries again.
- The backend team is told only on a change: "File ready to profile" on the feed's first fetched;
  "Source down" on the first failure after a success, or on a first fetch that fails.

Three parts (review fix 3): fetch_plan() reads; the caller ends that transaction; fetch_file() calls the
connector and stores the file with no database connection held; record_fetch() locks the feed row,
decides the notifications and writes. The file is stored before the database write; if the transaction
then fails, an orphan file is left (plan, risk 3). The step flushes and never commits.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.repositories.errors import FeedHasNoSource
from core.repositories.feeds import FeedRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.repositories.upload_sources import UploadSourceRepository
from core.steps.release import release_source
from core.steps.result import StepResult
from ports.connectors.base import SourceConnector, SourceUnavailable
from ports.connectors.catalogue import SourceCatalogue
from ports.notify.base import Notifier
from ports.storage.base import BlobStore

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class Fetched(StepResult):
    outcome: Literal["fetched"] = "fetched"
    feed_code: str
    fetch_id: UUID
    blob_ref: str  # the BlobStore key of the stored file
    size_bytes: int
    requirements_onboarding: list[UUID]  # the requirements this fetch moved to onboarding


class SourceDown(StepResult):
    outcome: Literal["source_down"] = "source_down"
    feed_code: str
    fetch_id: UUID
    detail: str
    released: list[UUID] = []  # 3b: requirements the N-day rule released (usually none)


FetchResult = Annotated[Fetched | SourceDown, Field(discriminator="outcome")]


def blob_key(feed_code: str, day: date, fetch_id: UUID, filename: str) -> str:
    """raw/<feed>/<date>/<fetch id>/<file>, with the file name cleaned into one safe key segment."""
    safe = _UNSAFE.sub("_", filename).lstrip(".") or "download"
    return f"raw/{feed_code}/{day.isoformat()}/{fetch_id}/{safe}"


def _release_after_n_days(session: Session, feed_id: UUID, n: int, *, catalogue: SourceCatalogue,
                          notifier: Notifier, backend_team: Sequence[str]) -> list[UUID]:
    """Per requirement: failed days since the later of its selection and the feed's last fetched.
    Only source_selected requirements: an onboarding one already has its file (assumption 51)."""
    choices, fetches, requirements = (SourceChoiceRepository(session), SourceFetchRepository(session),
                                      RequirementRepository(session))
    last_fetched = fetches.latest_fetched(feed_id)
    released = []
    for requirement_id in sorted(choices.requirements_selecting(feed_id)):  # id order (CLAUDE.md lock order)
        if requirements.lock(requirement_id).status != "source_selected":
            continue
        since = choices.current(requirement_id).created_at
        if last_fetched is not None and last_fetched.created_at > since:
            since = last_fetched.created_at
        if fetches.failed_days_since(feed_id, since) >= n:
            release_source(session, requirement_id=requirement_id, reason="source_down_n_days", user=None,
                           catalogue=catalogue, notifier=notifier, backend_team=backend_team)
            released.append(requirement_id)
    return released


class FetchPlan(BaseModel):
    """What one fetch needs, read from the feed. Built in a read transaction the caller then ends."""

    model_config = ConfigDict(frozen=True)

    feed_id: UUID
    feed_code: str
    connector: str
    source_ref: str
    fetch_id: UUID  # made first, so the blob key can carry it
    day: date
    # A dropzone feed: how the operator confirmed its files be read (sheet, header_row), from its upload source.
    intake_options: dict | None = None


class FetchAttempt(BaseModel):
    """The connector's answer, with the file already stored: a blob key and size, or why it failed."""

    model_config = ConfigDict(frozen=True)

    plan: FetchPlan
    blob_ref: str | None = None
    size_bytes: int | None = None
    detail: str | None = None


def fetch_plan(session: Session, *, feed_id: UUID, today: date | None = None) -> FetchPlan:
    """Read only. The caller ends the transaction before fetch_file(), so no database connection is held
    while the source answers (review fix 3, the pattern of model calls)."""
    feed = FeedRepository(session).get(feed_id)
    if feed.connector is None or feed.source_ref is None:
        raise FeedHasNoSource(f"Feed {feed.code!r} has no connector; there is nothing to fetch")
    options = None
    if feed.connector == "dropzone":  # task 4.4: its files are read as the first one was confirmed
        source = UploadSourceRepository(session).get_by_feed(feed.id)
        options = dict(source.intake_options) if source is not None else {}
    return FetchPlan(feed_id=feed.id, feed_code=feed.code, connector=feed.connector, source_ref=feed.source_ref,
                     fetch_id=uuid.uuid4(), day=today or datetime.now(UTC).date(), intake_options=options)


def fetch_file(plan: FetchPlan, *, connector_factory: Callable[[str], SourceConnector],
               blob_store: BlobStore) -> FetchAttempt:
    """No database: the connector call, then the file stored under its key."""
    with connector_factory(plan.connector) as connector:
        try:
            file = connector.fetch(plan.source_ref)
        except SourceUnavailable as exc:
            return FetchAttempt(plan=plan, detail=str(exc))
    if file.size == 0:
        return FetchAttempt(plan=plan, detail=f"{plan.source_ref} is empty")
    filename, content = file.filename, file.content
    if plan.connector == "dropzone":
        # Steps 12 and 13 read CSV: the file is stored as the canonical CSV, made by the code confirm_upload() uses.
        # Imported here because s00_intake imports this module for blob_key().
        from core.steps.s00_intake import NotATableResult, safe_name, to_canonical

        converted = to_canonical(content, filename, plan.intake_options)
        if isinstance(converted, NotATableResult):
            return FetchAttempt(plan=plan, detail=f"{filename}: {converted.detail}")
        content = converted[0]
        filename = f"{safe_name(filename).rsplit('.', 1)[0] or 'upload'}.csv"
    key = blob_key(plan.feed_code, plan.day, plan.fetch_id, filename)
    blob_store.put(key, content)
    return FetchAttempt(plan=plan, blob_ref=key, size_bytes=len(content))


def record_fetch(session: Session, attempt: FetchAttempt, *, notifier: Notifier, backend_team: Sequence[str],
                 release_after_days: int | None = None, catalogue: SourceCatalogue | None = None) -> Fetched | SourceDown:
    """Lock the feed row, then decide the notifications from what is committed now, then write. Two
    fetches of one feed at once (the first fetch and the daily job) are recorded one after the other,
    so only one of them sees a feed with no file yet (review fix 3).

    release_after_days (with catalogue): after a source_down, release every requirement that is
    source_selected on this feed and has failed that many UTC days in a row (assumption 51). None: no rule."""
    if release_after_days is not None and (release_after_days < 1 or catalogue is None):
        raise ValueError("release_after_days is at least 1 and needs the catalogue")
    plan = attempt.plan
    feed = FeedRepository(session).lock(plan.feed_id)
    fetches = SourceFetchRepository(session)
    previous = fetches.latest(feed.id)
    had_a_file = fetches.latest_fetched(feed.id) is not None

    def tell(subject: str, body: str) -> None:
        for user_id in backend_team:
            notifier.notify(recipient=user_id, subject=subject, body=body)

    if attempt.detail is not None:
        fetches.record(feed_id=feed.id, fetch_id=plan.fetch_id, outcome="source_down", detail=attempt.detail)
        if previous is None or previous.outcome == "fetched":
            tell(f"Source down: {feed.code}", f"The source of feed {feed.code} did not answer: {attempt.detail}. "
                                              "Amaze tries again every day.")
        released = []
        if release_after_days is not None:
            released = _release_after_n_days(session, feed.id, release_after_days, catalogue=catalogue,
                                             notifier=notifier, backend_team=backend_team)
        return SourceDown(feed_code=feed.code, fetch_id=plan.fetch_id, detail=attempt.detail, released=released)

    fetches.record(feed_id=feed.id, fetch_id=plan.fetch_id, outcome="fetched", blob_ref=attempt.blob_ref,
                   size_bytes=attempt.size_bytes)
    requirements = RequirementRepository(session)
    moved = []
    for requirement_id in sorted(SourceChoiceRepository(session).requirements_selecting(feed.id)):  # id order
        if requirements.lock(requirement_id).status == "source_selected":
            requirements.advance(requirement_id, "onboarding")
            moved.append(requirement_id)
    if not had_a_file:
        tell(f"File ready to profile: {feed.code}", f"The first file of candidate feed {feed.code} is stored "
                                                    f"as {attempt.blob_ref} ({attempt.size_bytes} bytes).")
    return Fetched(feed_code=feed.code, fetch_id=plan.fetch_id, blob_ref=attempt.blob_ref,
                   size_bytes=attempt.size_bytes, requirements_onboarding=moved)
