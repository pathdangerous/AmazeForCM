"""Known source?: does a source Amaze already uses have this data (phase 3 plan, assumptions 6 to 11).

Outcomes (CLAUDE.md): known_source -> 02; no_known_source -> S1. Deterministic code, no model call.

The rule: known_source when the requirement has elements, no ticked terms, and one known catalogue
source declares every element. With several, the fewest declared elements beyond the request, then
the code. A ticked term gives no_known_source: matching free text to a source is a judgement for S2,
where the requester sees the source.

known_source selects the source: the candidate feed for that source (created once, then reused), a
source_choices row, and the requirement moves from defined to source_selected. no_known_source writes
nothing. The first fetch is a separate step (s4_fetch), in its own transaction.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.domain.feed import Feed
from core.domain.requirement import Requirement
from core.domain.sourcing import SourceChoice, SourceSummary
from core.repositories.errors import RequirementNotInStatus, SourceRejected
from core.repositories.feeds import FeedRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.steps.result import StepResult
from ports.connectors.catalogue import SourceCatalogue, SourceEntry


class KnownSource(StepResult):
    outcome: Literal["known_source"] = "known_source"
    source: SourceSummary


class NoKnownSource(StepResult):
    outcome: Literal["no_known_source"] = "no_known_source"
    reason: Literal["terms_ticked", "no_known_source_covers"]


KnownSourceResult = Annotated[KnownSource | NoKnownSource, Field(discriminator="outcome")]


class FindSource(BaseModel):
    """The check's typed result and, for known_source, the candidate feed and the choice."""

    model_config = ConfigDict(frozen=True)

    check: KnownSourceResult
    feed: Feed | None = None
    choice: SourceChoice | None = None


def _known_entry(requirement: Requirement, catalogue: SourceCatalogue, rejected: frozenset[str]) -> SourceEntry | None:
    asked = set(requirement.elements)
    covering = [s for s in catalogue.known() if asked <= set(s.elements) and s.code not in rejected]
    return min(covering, key=lambda s: (len(set(s.elements) - asked), s.code)) if covering else None


def check_known_source(requirement: Requirement, catalogue: SourceCatalogue,
                       rejected: frozenset[str] = frozenset()) -> KnownSource | NoKnownSource:
    """The rule alone: reads nothing, writes nothing. rejected: sources whose feed the backend team rejected
    (phase 4 plan, assumption 25); they are not counted."""
    if requirement.terms or not requirement.elements:
        return NoKnownSource(reason="terms_ticked")
    entry = _known_entry(requirement, catalogue, rejected)
    if entry is None:
        return NoKnownSource(reason="no_known_source_covers")
    return KnownSource(source=SourceSummary.of(entry))


def lock_source(session: Session, code: str) -> None:
    """The advisory lock on a source code, held until the transaction ends. First in the lock order of every
    step that takes more than one lock (phase 4 plan, assumption 46): source code, feed row, requirements
    in id order. The key includes the schema (phase 7 plan, assumption 2a): advisory locks are database-wide,
    and one schema holds one deployment (two test workers hold two)."""
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(func.concat(func.current_schema(), ":", code)))))


def feed_for(session: Session, *, entry: SourceEntry, requirement_id: UUID) -> Feed:
    """The candidate feed of this source: the existing one, or a new one linked to the first requirement
    that selects it (one feed per source, assumption 11). Also used by S3.

    An advisory lock on the source code, held until the transaction ends, comes first: two requirements
    selecting the same new source at once then make one feed, not a unique violation (review fix 1). The
    callers take it already, before the requirement (assumption 46); taking it again here is a no-op."""
    lock_source(session, entry.code)
    feeds = FeedRepository(session)
    return feeds.get_by_source(entry.code) or feeds.create_candidate(
        code=entry.code, name=entry.name, requirement_id=requirement_id, source_code=entry.code,
        connector=entry.connector, source_ref=entry.source_ref,
    )


def select_source(session: Session, *, requirement: Requirement, entry: SourceEntry) -> tuple[Feed, SourceChoice]:
    """Hand the requirement to a source: its candidate feed (one per source) and the choice; the
    requirement moves to source_selected. Flushes; the caller commits."""
    feed = feed_for(session, entry=entry, requirement_id=requirement.id)
    choice = SourceChoiceRepository(session).record_known(
        requirement_id=requirement.id, feed_id=feed.id, source_code=entry.code
    )
    RequirementRepository(session).advance(requirement.id, "source_selected")
    return feed, choice


def find_known_source(session: Session, *, requirement_id: UUID, catalogue: SourceCatalogue) -> FindSource:
    """Known source? from a requirement with status defined. Raises RequirementNotInStatus otherwise,
    whatever the outcome would be, so a second click writes nothing.

    Lock order (phase 4 plan, assumption 46): the check is first made unlocked, to know which source to
    lock; then the source code, then the requirement; then the status and the rejected sources are read
    again. A source rejected in between is SourceRejected (409): the requester clicks again."""
    requirements, feeds = RequirementRepository(session), FeedRepository(session)
    first = check_known_source(requirements.get(requirement_id), catalogue, frozenset(feeds.rejected_source_codes()))
    if isinstance(first, KnownSource):
        lock_source(session, first.source.code)
    requirement = requirements.lock(requirement_id)
    if requirement.status != "defined":
        raise RequirementNotInStatus(
            f"Requirement {requirement_id} is {requirement.status}; Known source? starts from defined"
        )
    if isinstance(first, NoKnownSource):
        return FindSource(check=first)
    if first.source.code in feeds.rejected_source_codes():
        raise SourceRejected(f"Source {first.source.code} was rejected by the backend team meanwhile")
    feed, choice = select_source(session, requirement=requirement, entry=catalogue.get(first.source.code))
    return FindSource(check=first, feed=feed, choice=choice)
