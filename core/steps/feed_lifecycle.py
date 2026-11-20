"""Pause, resume and delete of a feed (phase 7.9; plan docs/plans/phase-7.9.md, sections 3 and 5).

Typed results: paused, resumed, deleted. Named refusals (never a bare error): FeedNotFound (404), FeedNotLive and
FeedNotPaused and LoadInProgress (409), NotOwnerOrOperations (403), ReasonNeeded (422). No model call: these are people
deciding, recorded. The owner of the feed or a user with the operations role may act, always with a reason.

Rules 1 and 7: a pause and a resume are recorded in feed_status_events before the stage moves (the database refuses a
move without its record). The delete is the one exception (CLAUDE.md): only amaze_delete_feed() deletes, and it writes
feed_deletions; this module gathers what the function cannot (blob keys, who to tell) before it runs, and deletes the
blobs after it. The step flushes; the caller commits.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from core.domain.feed import Feed
from core.repositories.errors import FeedNotFound, FeedNotLive, FeedNotPaused, LoadInProgress, NotOwnerOrOperations
from core.repositories.feeds import FeedRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.steps.errors import ReasonNeeded
from core.steps.known_source import lock_source
from ports.connectors.dropzone import remove_dropzone
from ports.identity.base import User
from ports.notify.base import Notifier
from ports.storage.base import BlobStore, InvalidBlobKey, check_key

log = logging.getLogger(__name__)

MAX_REASON = 500
RESET_FROM = ("source_selected", "onboarding", "ready", "access_restricted")


class FeedPaused(BaseModel):
    outcome: Literal["paused"] = "paused"
    feed_id: UUID
    code: str


class FeedResumed(BaseModel):
    outcome: Literal["resumed"] = "resumed"
    feed_id: UUID
    code: str


class FeedDeleted(BaseModel):
    outcome: Literal["deleted"] = "deleted"
    feed_id: UUID
    code: str
    rows_deleted: dict[str, int]  # per table, as feed_deletions records it
    blobs_deleted: int  # blobs and drop-folder files removed after the commit-able delete
    blobs_failed: int  # a blob that could not be removed: counted and logged; the deletion stands
    requirements_reset: int  # requirements that selected the feed, now no_source_found
    notified: int


def _reason(reason: str | None) -> str:
    text_ = (reason or "").strip()
    if not text_:
        raise ReasonNeeded("A reason that says why is needed")
    if len(text_) > MAX_REASON:
        raise ReasonNeeded(f"The reason is over {MAX_REASON} characters")
    return text_


def _may_act(feed: Feed, user: User) -> None:
    if "operations" in user.roles or (feed.owner_id is not None and feed.owner_id == user.id):
        return
    raise NotOwnerOrOperations(f"Only the owner of {feed.code} or the operations team may do this")


def _locked(session: Session, feed_id: UUID) -> Feed:
    feeds = FeedRepository(session)
    feeds.get(feed_id)  # FeedNotFound before any lock
    return feeds.lock(feed_id)


def pause_feed(session: Session, *, feed_id: UUID, user: User, reason: str) -> FeedPaused:
    """A live feed is not fetched until it is resumed; its product stays, with its last release."""
    reason = _reason(reason)
    feed = _locked(session, feed_id)
    _may_act(feed, user)
    if feed.stage != "registered":
        raise FeedNotLive(f"{feed.code} is {feed.stage}: only a live (registered) feed is paused")
    feeds = FeedRepository(session)
    feeds.record_status_event(feed_id, action="pause", reason=reason, by=user.id)
    feeds.set_stage(feed_id, "paused")
    return FeedPaused(feed_id=feed_id, code=feed.code)


def resume_feed(session: Session, *, feed_id: UUID, user: User, reason: str) -> FeedResumed:
    """The next daily run fetches the feed normally; nothing is fetched for the days it was paused."""
    reason = _reason(reason)
    feed = _locked(session, feed_id)
    _may_act(feed, user)
    if feed.stage != "paused":
        raise FeedNotPaused(f"{feed.code} is {feed.stage}: only a paused feed is resumed")
    feeds = FeedRepository(session)
    feeds.record_status_event(feed_id, action="resume", reason=reason, by=user.id)
    feeds.set_stage(feed_id, "registered")
    return FeedResumed(feed_id=feed_id, code=feed.code)


# ---------------------------------------------------------------- delete


def _key(ref: str | None) -> str | None:
    """A blob key from a stored reference ("local://raw/a.csv" or "raw/a.csv"); None if it is not a blob key."""
    if not ref:
        return None
    key = ref.removeprefix("local://")
    try:
        check_key(key)
    except InvalidBlobKey:
        return None
    return key


def _blob_keys(session: Session, feed_id: UUID) -> tuple[set[str], list[UUID]]:
    """The keys of every file the feed's rows name, and the proposal_log rows whose input and answer are blobs of its
    own (the ones the delete will remove are known only after it ran: the caller filters the log refs)."""
    params = {"f": feed_id}
    refs = set()
    for sql in ("SELECT file_ref FROM loads WHERE feed_id = :f",
                "SELECT blob_ref FROM source_fetches WHERE feed_id = :f",
                "SELECT original_ref FROM upload_sources WHERE feed_id = :f",
                "SELECT manifest_ref FROM upload_sources WHERE feed_id = :f"):
        refs.update(session.execute(text(sql), params).scalars())
    keys = {k for k in (_key(r) for r in refs) if k}
    logs = [i for i in session.execute(text(  # a glossary run not yet answered has no log row (NULL)
        "SELECT DISTINCT proposal_log_id FROM proposals WHERE feed_id = :f "
        "UNION SELECT proposal_log_id FROM glossary_runs WHERE feed_id = :f"), params).scalars() if i is not None]
    return keys, logs


def _log_keys(session: Session, log_ids: list[UUID]) -> dict[UUID, set[str]]:
    rows = session.execute(text("SELECT id, input_ref, raw_ref FROM proposal_log WHERE id = ANY(:ids)"),
                           {"ids": log_ids}).all()
    return {r.id: {k for k in (_key(r.input_ref), _key(r.raw_ref)) if k} for r in rows}


def _people_to_tell(session: Session, feed: Feed, requirement_ids: list[UUID]) -> list[str]:
    requirements = RequirementRepository(session)
    people = [requirements.get(i).requester_id for i in requirement_ids]
    product = session.execute(text("SELECT id, owner_id FROM products WHERE feed_id = :f"), {"f": feed.id}).first()
    if product is not None:
        people.append(product.owner_id)
        people += session.execute(text(
            "SELECT DISTINCT subject FROM product_grants WHERE product_id = :p AND subject_type = 'user'"),
            {"p": product.id}).scalars()
    return list(dict.fromkeys(people))


def delete_feed(session: Session, *, feed_id: UUID, user: User, reason: str, blob_store: BlobStore,
                dropzone_root: Path | None, notifier: Notifier) -> FeedDeleted:
    """Hard-delete a feed through amaze_delete_feed(): every row of the feed goes, glossary elements and the proposals
    they were made from stay, the deletion is recorded in feed_deletions. Requirements that selected the feed go back
    to no_source_found and their requesters are told, with the product's owner and grantees. Then the blobs go (after
    the database delete; a blob that cannot go is counted, not undone). LoadInProgress: a load is received."""
    reason = _reason(reason)
    feed = _locked_for_delete(session, feed_id)
    _may_act(feed, user)
    keys, log_ids = _blob_keys(session, feed_id)
    log_keys = _log_keys(session, log_ids) if log_ids else {}
    selecting = SourceChoiceRepository(session).requirements_selecting(feed_id)
    people = _people_to_tell(session, feed, selecting)

    try:
        with session.begin_nested():  # the caller's transaction survives a refusal
            counts = session.execute(select(func.amaze_delete_feed(feed_id, user.id, reason))).scalar_one()
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None)
        if code == "55006":
            raise LoadInProgress(f"{feed.code} has a load in progress: wait for it to finish") from exc
        if code == "P0002":
            raise FeedNotFound(f"No feed with id {feed_id}") from exc
        raise

    requirements = RequirementRepository(session)
    reset = 0
    for requirement_id in sorted(selecting):  # id order, as every step that locks more than one requirement
        requirements.lock(requirement_id)
        if requirements.get(requirement_id).status in RESET_FROM:
            requirements.advance(requirement_id, "no_source_found")
            reset += 1
    for person in people:
        notifier.notify(
            recipient=person, subject=f"Feed deleted: {feed.code}",
            body=(f"The feed {feed.code} was deleted by {user.display_name}: {reason} Its data is no longer available"
                  + (" and your request for it needs a new source." if reset else ".")), link=None)

    survivors = set(session.execute(text("SELECT id FROM proposal_log WHERE id = ANY(:ids)"),
                                    {"ids": log_ids}).scalars()) if log_ids else set()
    for log_id, refs in log_keys.items():
        if log_id not in survivors:  # a log row a kept proposal still names keeps its blobs
            keys |= refs
    deleted = failed = 0
    for key in sorted(keys):
        try:
            if blob_store.exists(key):
                blob_store.delete(key)
                deleted += 1
        except Exception:  # a blob that cannot go must not undo the delete
            log.exception("deleting blob %s of feed %s failed", key, feed.code)
            failed += 1
    if dropzone_root is not None:
        try:
            deleted += remove_dropzone(dropzone_root, feed.code)
        except Exception:
            log.exception("removing the drop folder of feed %s failed", feed.code)
            failed += 1
    return FeedDeleted(feed_id=feed_id, code=feed.code, rows_deleted={k: int(v) for k, v in counts.items()},
                       blobs_deleted=deleted, blobs_failed=failed, requirements_reset=reset, notified=len(people))


def _locked_for_delete(session: Session, feed_id: UUID) -> Feed:
    """Phase 4 lock order: the source's advisory lock, then the feed row."""
    feed = FeedRepository(session).get(feed_id)
    if feed.source_code:
        lock_source(session, feed.source_code)
    return FeedRepository(session).lock(feed_id)
