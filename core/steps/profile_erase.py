"""The recorded erase of a feed's old profiles (phase 7.16, T2; plan docs/plans/phase-7.16.md, 2.3 and 2.4).

A person (the feed's owner, or operations) names a profile version and a reason; every profile of the feed made before that
version goes, except the ones the database keeps (amaze_profile_keep_reason(): the latest, and any a registration, sign-off,
glossary run, arrival, code version or report points to). The delete is done by amaze_erase_profiles() alone, which writes
the record (profile_erasures). Typed results: profiles_erased, nothing_to_erase (every profile before the version is kept, or
there is none). Named refusals: FeedNotFound (404), NotOwnerOrOperations (403), ReasonNeeded and UnknownProfileVersion (422).
No model call. The step flushes; the caller commits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.tables import ProfileErasureRow
from core.steps.errors import UnknownProfileVersion
from core.steps.feed_lifecycle import _may_act, _reason  # the same people and the same reason as a pause or a delete
from ports.identity.base import User


class KeptProfile(BaseModel):
    id: UUID
    version: int
    reason: str  # latest, registration, signoff, glossary_run, load_arrival, code_version or report


class ProfilesErased(BaseModel):
    outcome: Literal["profiles_erased"] = "profiles_erased"
    erasure_id: UUID
    feed_id: UUID
    erased_versions: list[int]
    kept: list[KeptProfile]  # the profiles before the cutoff that stayed, with why


class NothingToErase(BaseModel):
    outcome: Literal["nothing_to_erase"] = "nothing_to_erase"
    kept: list[KeptProfile]
    detail: str


ProfileErasureResult = Annotated[ProfilesErased | NothingToErase, Field(discriminator="outcome")]


class ProfileErasure(BaseModel):
    """One recorded erase, as the feed page shows it."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    at: datetime
    by: str
    reason: str
    erased_versions: list[int]
    erased_count: int
    kept_count: int


def keep_reasons(session: Session, feed_id: UUID) -> dict[UUID, str | None]:
    """Read only. Why each profile of the feed may not be erased now (None: it may), from the database's own function."""
    rows = session.execute(text("SELECT id, amaze_profile_keep_reason(id) FROM feed_profiles WHERE feed_id = :f"),
                           {"f": feed_id})
    return {row[0]: row[1] for row in rows}


def erasures_of(session: Session, feed_id: UUID) -> list[ProfileErasure]:
    rows = session.scalars(select(ProfileErasureRow).where(ProfileErasureRow.feed_id == feed_id)
                           .order_by(ProfileErasureRow.at, ProfileErasureRow.id))
    return [ProfileErasure.model_validate(r) for r in rows]


def erase_profiles(session: Session, *, feed_id: UUID, user: User, before_version: int,
                   reason: str) -> ProfilesErased | NothingToErase:
    """Erase the feed's profiles made before `before_version` that nothing keeps."""
    reason = _reason(reason)
    feed = FeedRepository(session).get(feed_id)  # FeedNotFound
    _may_act(feed, user)  # NotOwnerOrOperations
    cutoff = next((p.created_at for p in FeedProfileRepository(session).history(feed_id) if p.version == before_version), None)
    if cutoff is None:
        raise UnknownProfileVersion(f"{feed.code} has no profile {before_version}")
    try:
        with session.begin_nested():  # the caller's transaction survives a refusal
            done = session.execute(select(func.amaze_erase_profiles(feed_id, cutoff, reason, user.id))).scalar_one()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "P0002" and "nothing to erase" in str(exc.orig):
            kept = session.execute(text(
                "SELECT id, version, amaze_profile_keep_reason(id) FROM feed_profiles WHERE feed_id = :f "
                "AND created_at < :c ORDER BY version"), {"f": feed_id, "c": cutoff})
            return NothingToErase(kept=[KeptProfile(id=i, version=v, reason=r) for i, v, r in kept if r is not None],
                                  detail=f"Every profile of {feed.code} before profile {before_version} is kept, or there is none.")
        raise
    return ProfilesErased(erasure_id=done["erasure_id"], feed_id=feed_id, erased_versions=done["erased_versions"],
                          kept=[KeptProfile(**k) for k in done["kept"]])
