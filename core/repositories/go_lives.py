"""GoLiveRepository: the data owner's approvals of a feed's configuration (step 11, phase 6). Versioned per
feed, append-only; trg_feed_go_lives_b_guard (migration 0018, G6) holds the rules."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.domain.go_live import GoLive
from core.repositories.errors import FeedNotFound, ProposalForAnotherFeed
from core.repositories.proposals import ProposalRepository
from core.repositories.tables import FeedGoLiveRow as Row
from core.repositories.tables import FeedRow


class GoLiveRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_version(self, *, feed_id: UUID, proposal_id: UUID, approved_by: str, mapping_ids: Sequence[UUID],
                    rule_ids: Sequence[UUID], test_load_id: UUID, hypercare_until: date,
                    code_version_id: UUID, transformation_ids: Sequence[UUID] = ()) -> GoLive:
        """The feed's next go-live, from an approved review_summary proposal of this feed. The caller holds the
        feed row lock; the database checks the rest (the decider, the owner, the pinned ids, the test load)."""
        if self._session.get(FeedRow, feed_id) is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        proposal = ProposalRepository(self._session).require_approved(proposal_id, kind="review_summary")
        if proposal.feed_id != feed_id:
            raise ProposalForAnotherFeed(f"Proposal {proposal_id} is for feed {proposal.feed_id}, not {feed_id}")
        latest = self._session.scalar(select(func.max(Row.version)).where(Row.feed_id == feed_id))
        row = Row(feed_id=feed_id, version=(latest or 0) + 1, proposal_id=proposal_id, approved_by=approved_by,
                  mapping_ids=list(mapping_ids), rule_ids=list(rule_ids), test_load_id=test_load_id,
                  hypercare_until=hypercare_until, transformation_ids=list(transformation_ids),
                  code_version_id=code_version_id)
        self._session.add(row)
        self._session.flush()
        return GoLive.model_validate(row)

    def latest(self, feed_id: UUID) -> GoLive | None:
        row = self._session.scalars(
            select(Row).where(Row.feed_id == feed_id).order_by(Row.version.desc()).limit(1)).one_or_none()
        return None if row is None else GoLive.model_validate(row)
