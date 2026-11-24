"""Profile again (phase 7.16): step 02 on a registered feed's latest file, as a new profile version.

Nothing else is written: no summary, no sign-off. The new profile becomes the template step 12 compares each delivery with,
so it is taken only when the file has exactly the columns of the feed's current profile; a changed structure goes through
step 12 (parked or held) and steps 05 and 06, never through here. Typed results: profiled_again, profile_differs (the
columns that were added or are missing, or why the file gave nothing to profile). Named refusals: FeedNotFound, FeedNotRegistered,
FeedHasNoFile, FeedNotProfiled. No model call. The caller reads the plan, ends its transaction, runs run_profile() with no
connection held, then records. The step flushes; the caller commits.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy.orm import Session

from core.domain.profiling import FeedProfile
from core.repositories.errors import FeedNotProfiled, FeedNotRegistered
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.steps.result import StepResult
from core.steps.s02_profile import ProfileAttempt, ProfilePlan, profile_plan, record_profile
from ports.connectors.catalogue import SourceCatalogue


class ProfiledAgain(StepResult):
    outcome: Literal["profiled_again"] = "profiled_again"
    profile: FeedProfile


class ProfileDiffers(StepResult):
    outcome: Literal["profile_differs"] = "profile_differs"
    added: list[str]  # columns the file has and the current profile has not
    missing: list[str]  # columns of the current profile the file lacks
    detail: str


ProfileAgainResult = Annotated[ProfiledAgain | ProfileDiffers, Field(discriminator="outcome")]


def again_plan(session: Session, *, feed_id: UUID, catalogue: SourceCatalogue) -> ProfilePlan:
    """Read only."""
    feed = FeedRepository(session).get(feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}; only a registered feed is profiled again")
    if FeedProfileRepository(session).latest(feed_id) is None:
        raise FeedNotProfiled(f"Feed {feed.code!r} has no profile to repeat")
    return profile_plan(session, feed_id=feed_id, catalogue=catalogue, stage="registered")


def profile_again(session: Session, attempt: ProfileAttempt, *, profiled_by: str) -> ProfiledAgain | ProfileDiffers:
    feed = FeedRepository(session).lock(attempt.plan.feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} became {feed.stage} during the profile; nothing is written")
    current = {c.name for c in FeedProfileRepository(session).latest(feed.id).columns}
    found = {c.name for c in attempt.file.columns} if attempt.file else set()
    if attempt.detail is not None:
        return ProfileDiffers(added=[], missing=sorted(current - found), detail=f"Nothing was profiled: {attempt.detail}")
    if found != current:
        return ProfileDiffers(added=sorted(found - current), missing=sorted(current - found),
                              detail="The file's columns differ from the feed's current profile. A changed structure goes "
                                     "through the load (step 12), not through a new profile.")
    return ProfiledAgain(profile=record_profile(session, attempt, profiled_by=profiled_by, stage="registered").profile)
