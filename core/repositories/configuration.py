"""MappingRepository and RuleRepository: versioned feed configuration, created only from approved
proposals (rule 1) and never edited in place (rule 7)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from core.domain.configuration import Mapping, MappingDraft, Rule, RuleDraft
from core.domain.proposal import Proposal, ProposalKind
from core.repositories.errors import (
    FeedNotFound,
    FeedNotRegistered,
    NotAProposalOption,
    ProposalForAnotherFeed,
    ProposalForAnotherField,
)
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.tables import FeedRow, MappingRow, RuleRow


def _check_feed_and_proposal(session: Session, feed_id: UUID, proposal_id: UUID, kind: ProposalKind) -> None:
    if session.get(FeedRow, feed_id) is None:
        raise FeedNotFound(f"No feed with id {feed_id}")
    proposal = ProposalRepository(session).require_approved(proposal_id, kind=kind)
    if proposal.feed_id is not None and proposal.feed_id != feed_id:
        raise ProposalForAnotherFeed(f"Proposal {proposal_id} was approved for feed {proposal.feed_id}, not {feed_id}")


SYSTEM = "system"  # the actor that rejects a pending proposal so that its step can run again (Back, a stale rerun)


def _by_a_person(session: Session, rejected: list[Proposal]) -> list[Proposal]:
    """The rejected proposals a person rejected (phase 7.13, T4): a rejection by `system` is no judgement on the
    proposal, so it does not stop the agent proposing the same again."""
    proposals = ProposalRepository(session)
    return [p for p in rejected
            if any(a.decision == "rejected" and a.decided_by != SYSTEM for a in proposals.approvals(p.id))]


class MappingRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_version(self, *, feed_id: UUID, source_field: str, element_key: str, proposal_id: UUID) -> Mapping:
        """The column's next version, from an approved mapping proposal of this feed and column naming this
        element among its candidates, on a registered feed (phase 5 plan, assumption 32; trigger
        trg_mappings_b_matches_proposal checks the same). The caller holds the feed row lock."""
        draft = MappingDraft(source_field=source_field, element_key=element_key)
        feed = self._session.get(FeedRow, feed_id)
        if feed is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        proposal = ProposalRepository(self._session).require_approved(proposal_id, kind="mapping")
        if proposal.feed_id != feed_id:
            raise ProposalForAnotherFeed(f"Proposal {proposal_id} is for feed {proposal.feed_id}, not {feed_id}")
        if proposal.payload.get("source_field") != draft.source_field:
            raise ProposalForAnotherField(f"Proposal {proposal_id} is for column "
                                          f"{proposal.payload.get('source_field')!r}, not {draft.source_field!r}")
        if draft.element_key not in proposal.payload.get("candidates", []):
            raise NotAProposalOption(f"{draft.element_key} is not a candidate of proposal {proposal_id}")
        if feed.stage != "registered":
            raise FeedNotRegistered(f"Feed {feed_id} is {feed.stage}, not registered")
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

    def uses_by_element(self) -> dict[str, int]:
        """How many columns (the latest mapping of each feed column) name each glossary element (phase 7.19: "used by N
        mappings" on an upload's update row)."""
        latest = (select(MappingRow.element_key).ext(distinct_on(MappingRow.feed_id, MappingRow.source_field))
                  .order_by(MappingRow.feed_id, MappingRow.source_field, MappingRow.version.desc()).subquery())
        return {key: count for key, count in self._session.execute(
            select(latest.c.element_key, func.count()).group_by(latest.c.element_key))}

    def history(self, feed_id: UUID, source_field: str) -> list[Mapping]:
        query = (
            select(MappingRow)
            .where(MappingRow.feed_id == feed_id, MappingRow.source_field == source_field)
            .order_by(MappingRow.version)
        )
        return [Mapping.model_validate(r) for r in self._session.scalars(query)]

    def rejected_elements(self, feed_id: UUID, source_field: str) -> set[str]:
        """Every element a rejected mapping proposal of this column offered: not proposed again (assumption 27)."""
        rejected = ProposalRepository(self._session).list_for_feed_column("mapping", feed_id, source_field,
                                                                          status="rejected")
        return {key for p in _by_a_person(self._session, rejected) for key in p.payload.get("candidates", [])}


class RuleRepository:
    def __init__(self, session: Session):
        self._session = session

    def add_version(self, *, feed_id: UUID, rule_key: str, sql: str, description: str, proposal_id: UUID) -> Rule:
        """The rule's next version, from an approved rule proposal of this feed that holds exactly this key, SQL
        and description, on a registered feed (phase 6 plan, assumption 32; trigger trg_rules_b_matches_proposal
        checks the same). The caller holds the feed row lock."""
        draft = RuleDraft(rule_key=rule_key, sql=sql, description=description)
        _check_feed_and_proposal(self._session, feed_id, proposal_id, "rule")
        proposal = ProposalRepository(self._session).get(proposal_id)
        for name, wanted in (("rule_key", draft.rule_key), ("sql", draft.sql), ("description", draft.description)):
            if proposal.payload.get(name) != wanted:
                raise ProposalForAnotherField(f"Proposal {proposal_id} holds another {name} than the rule")
        if self._session.get(FeedRow, feed_id).stage != "registered":
            raise FeedNotRegistered(f"Feed {feed_id} is not registered")
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

    def rejected_keys(self, feed_id: UUID) -> set[str]:
        """The rule keys of this feed's rejected rule proposals: not proposed again (phase 6 plan, assumption 11)."""
        rejected = ProposalRepository(self._session).list_for_feed(feed_id, "rule", status="rejected")
        return {p.payload["rule_key"] for p in _by_a_person(self._session, rejected)}

    def history(self, feed_id: UUID, rule_key: str) -> list[Rule]:
        query = (
            select(RuleRow).where(RuleRow.feed_id == feed_id, RuleRow.rule_key == rule_key).order_by(RuleRow.version)
        )
        return [Rule.model_validate(r) for r in self._session.scalars(query)]
