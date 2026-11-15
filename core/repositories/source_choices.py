"""SourceChoiceRepository: which source, and so which candidate feed, a requirement was handed to.

Versioned per requirement (phase 3 plan, assumptions 49 to 53): every write is the requirement's next
version, a selection (a known source, or the requester's pick) or a release. The highest version is
current. Insert only (source_choices is append-only); the database guards the same rules (0009). The
repository flushes and never commits.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from core.domain.sourcing import RELEASE_REASONS, ReleaseReason, SourceChoice
from core.repositories.errors import (
    FeedNotFound,
    NothingToRelease,
    RequirementAlreadyHasSource,
    RequirementNotFound,
    SourceAlreadyReleased,
)
from core.repositories.tables import FeedRow, RequirementRow
from core.repositories.tables import SourceChoiceRow as Row
from ports.connectors.catalogue import SourceConditions


class SourceChoiceRepository:
    def __init__(self, session: Session):
        self._session = session

    # ---------------------------------------------------------------- writes

    def record_known(self, *, requirement_id: UUID, feed_id: UUID, source_code: str) -> SourceChoice:
        """The requirement is handed to a known source (Known source? -> known_source)."""
        version = self._next_selection(requirement_id, feed_id, source_code)
        return self._add(requirement_id=requirement_id, version=version, action="selected", feed_id=feed_id,
                         source_code=source_code, basis="known_source")

    def record_pick(self, *, requirement_id: UUID, feed_id: UUID, source_code: str, proposal_id: UUID,
                    accepted_conditions: SourceConditions, decided_by: str) -> SourceChoice:
        """The requester picked a listed source and accepted its conditions (S3 -> source_picked)."""
        if not decided_by.strip():
            raise ValueError("a pick names the requester who made it")
        version = self._next_selection(requirement_id, feed_id, source_code)
        return self._add(requirement_id=requirement_id, version=version, action="selected", feed_id=feed_id,
                         source_code=source_code, basis="requester_pick", proposal_id=proposal_id,
                         accepted_conditions=accepted_conditions.model_dump(mode="json"), decided_by=decided_by)

    def record_release(self, *, requirement_id: UUID, reason: ReleaseReason,
                       decided_by: str | None = None) -> SourceChoice:
        """Release the current selection: its feed and source, as the next version."""
        if reason not in RELEASE_REASONS:
            raise ValueError(f"unknown release reason {reason!r}")
        if (reason == "requester_asked") != bool(decided_by and decided_by.strip()):
            raise ValueError("a requester_asked release names the requester; a release by rule "
                             "(source_down_n_days, candidate_rejected) names nobody")
        self._require_requirement(requirement_id)
        current = self.current(requirement_id)
        if current is None or current.action != "selected":
            raise NothingToRelease(f"Requirement {requirement_id} has no selected source to release")
        return self._add(requirement_id=requirement_id, version=current.version + 1, action="released",
                         feed_id=current.feed_id, source_code=current.source_code, reason=reason,
                         decided_by=decided_by)

    # ---------------------------------------------------------------- reads

    def current(self, requirement_id: UUID) -> SourceChoice | None:
        """The highest version, or None if the requirement was never handed to a source."""
        row = self._session.scalars(
            select(Row).where(Row.requirement_id == requirement_id).order_by(Row.version.desc()).limit(1)
        ).first()
        return None if row is None else SourceChoice.model_validate(row)

    def history(self, requirement_id: UUID) -> list[SourceChoice]:
        """Every version, oldest first."""
        rows = self._session.scalars(select(Row).where(Row.requirement_id == requirement_id).order_by(Row.version))
        return [SourceChoice.model_validate(r) for r in rows]

    def released_sources(self, requirement_id: UUID) -> set[str]:
        query = select(Row.source_code).where(Row.requirement_id == requirement_id, Row.action == "released")
        return set(self._session.scalars(query))

    def requirements_selecting(self, feed_id: UUID) -> list[UUID]:
        """The requirements whose current choice is a selection of this feed, oldest selection first."""
        latest = (select(Row.requirement_id, func.max(Row.version).label("version"))
                  .group_by(Row.requirement_id).subquery())
        current = aliased(Row)
        query = (
            select(current.requirement_id)
            .join(latest, (latest.c.requirement_id == current.requirement_id) & (latest.c.version == current.version))
            .where(current.feed_id == feed_id, current.action == "selected")
            .order_by(current.created_at, current.id)
        )
        return list(self._session.scalars(query))

    # ---------------------------------------------------------------- helpers

    def _next_selection(self, requirement_id: UUID, feed_id: UUID, source_code: str) -> int:
        self._require_requirement(requirement_id)
        feed = self._session.get(FeedRow, feed_id)
        if feed is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        if feed.source_code != source_code:
            raise ValueError(f"Feed {feed.code!r} reads {feed.source_code!r}, not {source_code!r}")
        current = self.current(requirement_id)
        if current is not None and current.action == "selected":
            raise RequirementAlreadyHasSource(f"Requirement {requirement_id} is already handed to a source")
        if source_code in self.released_sources(requirement_id):
            raise SourceAlreadyReleased(f"Requirement {requirement_id} released {source_code}; it is not offered again")
        return 1 if current is None else current.version + 1

    def _require_requirement(self, requirement_id: UUID) -> None:
        # Locked, so two requests cannot both write the next version of one requirement's choices.
        row = self._session.scalars(
            select(RequirementRow).where(RequirementRow.id == requirement_id).with_for_update()
        ).one_or_none()
        if row is None:
            raise RequirementNotFound(f"No requirement with id {requirement_id}")

    def _add(self, **fields) -> SourceChoice:
        row = Row(**fields)
        self._session.add(row)
        self._session.flush()
        return SourceChoice.model_validate(row)
