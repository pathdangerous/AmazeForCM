"""MappingRepository and RuleRepository: versioned feed configuration, created only from approved
proposals (rule 1) and never edited in place (rule 7)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from core.domain.configuration import Mapping, MappingDraft, Rule, RuleDraft
from core.domain.proposal import ProposalKind
from core.repositories.errors import FeedNotFound, ProposalForAnotherFeed
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.tables import FeedRow, MappingRow, RuleRow


def _check_feed_and_proposal(session: Session, feed_id: UUID, proposal_id: UUID, kind: ProposalKind) -> None:
    if session.get(FeedRow, feed_id) is None:
        raise FeedNotFound(f"No feed with id {feed_id}")
    proposal = ProposalRepository(session).require_approved(proposal_id, kind=kind)
    if proposal.feed_id is not None and proposal.feed_id != feed_id:
        raise ProposalForAnotherFeed(f"Proposal {proposal_id} was approved for feed {proposal.feed_id}, not {feed_id}")


class MappingRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_version(self, *, feed_id: UUID, source_field: str, element_key: str, proposal_id: UUID) -> Mapping:
        draft = MappingDraft(source_field=source_field, element_key=element_key)
        _check_feed_and_proposal(self._session, feed_id, proposal_id, "mapping")
        GlossaryRepository(self._session).get_current(draft.element_key)  # raises UnknownGlossaryElement
        latest = self._session.scalar(
            select(func.max(MappingRow.version)).where(
                MappingRow.feed_id == feed_id, MappingRow.source_field == draft.source_field
            )
        )
        row = MappingRow(feed_id=feed_id, version=(latest or 0) + 1, proposal_id=proposal_id, **draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return Mapping.model_validate(row)

    def current_for_feed(self, feed_id: UUID) -> list[Mapping]:
        """The highest version of every source field of the feed, by source field."""
        query = (
            select(MappingRow)
            .where(MappingRow.feed_id == feed_id)
            .ext(distinct_on(MappingRow.source_field))
            .order_by(MappingRow.source_field, MappingRow.version.desc())
        )
        return [Mapping.model_validate(r) for r in self._session.scalars(query)]

    def history(self, feed_id: UUID, source_field: str) -> list[Mapping]:
        query = (
            select(MappingRow)
            .where(MappingRow.feed_id == feed_id, MappingRow.source_field == source_field)
            .order_by(MappingRow.version)
        )
        return [Mapping.model_validate(r) for r in self._session.scalars(query)]


class RuleRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_version(self, *, feed_id: UUID, rule_key: str, sql: str, description: str, proposal_id: UUID) -> Rule:
        draft = RuleDraft(rule_key=rule_key, sql=sql, description=description)
        _check_feed_and_proposal(self._session, feed_id, proposal_id, "rule")
        latest = self._session.scalar(
            select(func.max(RuleRow.version)).where(RuleRow.feed_id == feed_id, RuleRow.rule_key == draft.rule_key)
        )
        row = RuleRow(feed_id=feed_id, version=(latest or 0) + 1, proposal_id=proposal_id, **draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return Rule.model_validate(row)

    def current_for_feed(self, feed_id: UUID) -> list[Rule]:
        """The highest version of every rule of the feed, by rule key."""
        query = (
            select(RuleRow)
            .where(RuleRow.feed_id == feed_id)
            .ext(distinct_on(RuleRow.rule_key))
            .order_by(RuleRow.rule_key, RuleRow.version.desc())
        )
        return [Rule.model_validate(r) for r in self._session.scalars(query)]

    def history(self, feed_id: UUID, rule_key: str) -> list[Rule]:
        query = (
            select(RuleRow).where(RuleRow.feed_id == feed_id, RuleRow.rule_key == rule_key).order_by(RuleRow.version)
        )
        return [Rule.model_validate(r) for r in self._session.scalars(query)]
