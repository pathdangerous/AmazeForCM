"""Step 03 sign-off: the backend team decides a candidate feed's latest profile (phase 4 plan, assumptions 18
to 27; decision D1).

Outcomes (CLAUDE.md): meets; meets_with_gaps; rejected.

- The person chooses the outcome and writes the gaps or reasons. Their decision on the ProfilerAgent's
  summary follows D1: approved when the outcome is the recommended one, rejected when it differs. No
  sign-off without a summary (Q3: rule 4's audit trail and one decision path).
- meets: the feed stays a candidate, ready to register; the requester sees nothing new.
- meets_with_gaps: the same, and each requester whose requirement selects the feed now is told the gaps.
- rejected: the feed is rejected (final in phase 4); each requirement that selects it now is released
  (candidate_rejected) to awaiting_source_choice or no_source_found, and its requester is told the reasons.
- Who selects the feed is requirements_selecting(), never feeds.requirement_id (note 4b).

Lock order (assumption 46): the source code, then the feed row, then the summary's proposal row
(record_decision), then the requirements in id order. Only then are the next version and the status read.
Rules execute: no model call. The step flushes; the caller commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy.orm import Session

from core.domain.profiling import FeedSignoff, SignoffOutcome
from core.repositories.errors import FeedNotACandidate, FeedNotProfiled, ProfileNotSummarised, ProposalAlreadyDecided
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feed_signoffs import FeedSignoffRepository
from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.steps.errors import StepInputError
from core.steps.known_source import lock_source
from core.steps.release import Released, release_source
from core.steps.requirement_page import accepted_with_gaps_message, rejected_message
from core.steps.result import StepResult
from core.steps.s02_profile import summary_of
from ports.connectors.catalogue import SourceCatalogue, UnknownSource
from ports.identity.base import User
from ports.notify.base import Notifier


class SignoffDoesNotFit(StepInputError):
    """The gaps or reasons do not fit the outcome, or meets on a spec-only profile (422)."""


class Meets(StepResult):
    outcome: Literal["meets"] = "meets"
    signoff: FeedSignoff


class MeetsWithGaps(StepResult):
    outcome: Literal["meets_with_gaps"] = "meets_with_gaps"
    signoff: FeedSignoff  # holds the gaps
    told: list[UUID]  # requirements whose requester was told


class Rejected(StepResult):
    outcome: Literal["rejected"] = "rejected"
    signoff: FeedSignoff  # holds the reasons
    released: list[Released]  # one per requirement that selected the feed


SignoffResult = Annotated[Meets | MeetsWithGaps | Rejected, Field(discriminator="outcome")]


def _fits(outcome: SignoffOutcome, gaps: list[str], reasons: list[str]) -> None:
    if any(not text for text in gaps + reasons):
        raise SignoffDoesNotFit("A gap or reason cannot be empty")
    fits = {"meets": not gaps and not reasons, "meets_with_gaps": bool(gaps) and not reasons,
            "rejected": bool(reasons) and not gaps}.get(outcome)
    if not fits:
        raise SignoffDoesNotFit("meets has no gap or reason; meets_with_gaps needs gaps and no reason; "
                                "rejected needs reasons and no gap")


def sign_off(session: Session, *, feed_id: UUID, user: User, outcome: SignoffOutcome, gaps: Sequence[str],
             reasons: Sequence[str], catalogue: SourceCatalogue, notifier: Notifier,
             backend_team: Sequence[str]) -> Meets | MeetsWithGaps | Rejected:
    gaps, reasons = [g.strip() for g in gaps], [r.strip() for r in reasons]
    _fits(outcome, gaps, reasons)
    feeds = FeedRepository(session)
    code = feeds.get(feed_id).source_code
    if code:
        lock_source(session, code)
    feed = feeds.lock(feed_id)  # before the latest profile, the summary's status and the next version are read
    if feed.stage != "candidate":
        raise FeedNotACandidate(f"Feed {feed.code!r} is {feed.stage}; only a candidate feed is signed off")
    profile = FeedProfileRepository(session).latest(feed.id)
    if profile is None:
        raise FeedNotProfiled(f"Feed {feed.code!r} has no profile to sign off")
    if profile.outcome == "spec_only_low_confidence" and outcome == "meets":
        raise SignoffDoesNotFit("A spec-only profile cannot meet the requirement: no data rows were profiled")
    proposal = summary_of(session, feed_id=feed.id, profile_id=profile.id)
    if proposal is None:
        raise ProfileNotSummarised(f"Profile {profile.version} of feed {feed.code!r} has no summary yet")
    if proposal.status != "pending":
        raise ProposalAlreadyDecided(f"The summary of profile {profile.version} of feed {feed.code!r} is "
                                     f"already {proposal.status}")
    agrees = outcome == proposal.payload["recommended_outcome"]  # decision D1
    ProposalRepository(session).record_decision(proposal.id, user=user, decision="approved" if agrees else "rejected",
                                                note=f"Signed off as {outcome}")
    signoff = FeedSignoffRepository(session).add_version(
        feed_id=feed.id, profile_id=profile.id, proposal_id=proposal.id, outcome=outcome, gaps=gaps, reasons=reasons,
        signed_off_by=user.id)
    if outcome == "meets":
        return Meets(signoff=signoff)

    try:
        name = catalogue.get(code).name if code else feed.name
    except UnknownSource:
        name = code
    requirements = RequirementRepository(session)
    selecting = sorted(SourceChoiceRepository(session).requirements_selecting(feed.id))  # id order
    if outcome == "meets_with_gaps":
        note = accepted_with_gaps_message(name, gaps)
        for requirement_id in selecting:
            notifier.notify(recipient=requirements.get(requirement_id).requester_id,
                            subject=f"Accepted with gaps: {name}", body=note)
        return MeetsWithGaps(signoff=signoff, told=selecting)

    feeds.reject(feed.id)
    choices, released = SourceChoiceRepository(session), []
    for requirement_id in selecting:
        requirement = requirements.lock(requirement_id)
        current = choices.current(requirement_id)
        if current is None or current.action != "selected" or current.feed_id != feed.id:
            continue  # released by its requester while this sign-off waited for the lock
        result = release_source(session, requirement_id=requirement_id, reason="candidate_rejected", user=None,
                                catalogue=catalogue, notifier=notifier, backend_team=backend_team)
        notifier.notify(recipient=requirement.requester_id, subject=f"Source rejected: {name}",
                        body=rejected_message(name, reasons, source_left=result.status == "awaiting_source_choice"))
        released.append(result)
    return Rejected(signoff=signoff, released=released)
