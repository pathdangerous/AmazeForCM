"""TransformationRepository and KeyPolicyRepository: versioned feed configuration (phase 7.8, assumptions 28, 29).

Transformations are created only from approved proposals (rule 1) and never edited (rule 7). The caller holds the
feed row lock, so the next version is read after it (CLAUDE.md, lock order).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from core.domain.transformation import (
    KeyPolicy,
    KeyPolicyDraft,
    RejectedTransform,
    Transformation,
    TransformationDraft,
)
from core.repositories.configuration import _by_a_person
from core.repositories.errors import (
    FeedNotFound,
    FeedNotRegistered,
    ProposalForAnotherFeed,
    ProposalForAnotherField,
    SourceFieldNotMapped,
)
from core.repositories.proposals import ProposalRepository
from core.repositories.tables import FeedKeyPolicyRow, FeedRow, MappingRow, TransformationRow


class TransformationRepository:
    def __init__(self, session: Session):
        self._session = session

    def rejected_steps(self, feed_id: UUID) -> list[RejectedTransform]:
        """Phase 7.22, T5: per column, the ops of the transformation proposals a person rejected (with the reason they
        gave): not proposed again. A rejection by `system` (Back) is no judgement and does not count, as for mappings
        (7.13 T4); nor does a rejected fix of a held load (7.14), which is a different question."""
        proposals = ProposalRepository(self._session)
        rejected = [p for p in proposals.list_for_feed(feed_id, "transformation", "rejected")
                    if not p.payload.get("exception_group")]
        found: dict[str, RejectedTransform] = {}
        for p in _by_a_person(self._session, rejected):
            reason = next((a.note for a in reversed(proposals.approvals(p.id)) if a.decision == "rejected" and a.note), "")
            column = p.payload["source_field"]
            ops = [s["op"] for s in p.payload.get("steps", [])]
            before = found.get(column)
            found[column] = RejectedTransform(source_field=column, ops=list(dict.fromkeys([*(before.ops if before else []), *ops])),
                                              reason=reason or (before.reason if before else ""))
        return list(found.values())

    def add_version(self, *, feed_id: UUID, source_field: str, steps: Sequence[Any], proposal_id: UUID,
                    created_by: str) -> Transformation:
        """The column's next version, from an approved transformation proposal of this feed and column that holds
        exactly these steps, on a registered feed, for a mapped column (trigger
        trg_transformations_b_matches_proposal checks the same)."""
        draft = TransformationDraft(feed_id=feed_id, source_field=source_field, steps=list(steps))
        feed = self._session.get(FeedRow, feed_id)
        if feed is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        proposal = ProposalRepository(self._session).require_approved(proposal_id, kind="transformation")
        if proposal.feed_id != feed_id:
            raise ProposalForAnotherFeed(f"Proposal {proposal_id} is for feed {proposal.feed_id}, not {feed_id}")
        if proposal.payload.get("source_field") != draft.source_field:
            raise ProposalForAnotherField(f"Proposal {proposal_id} is for column "
                                          f"{proposal.payload.get('source_field')!r}, not {draft.source_field!r}")
        steps_json = draft.steps_json()
        if proposal.payload.get("steps") != steps_json:
            raise ProposalForAnotherField(f"Proposal {proposal_id} holds other steps than the transformation")
        if feed.stage != "registered":
            raise FeedNotRegistered(f"Feed {feed_id} is {feed.stage}, not registered")
        if not self._session.scalar(select(exists().where(MappingRow.feed_id == feed_id,
                                                          MappingRow.source_field == draft.source_field))):
            raise SourceFieldNotMapped(f"Column {draft.source_field!r} of feed {feed_id} is not mapped")
        latest = self._session.scalar(select(func.max(TransformationRow.version)).where(
            TransformationRow.feed_id == feed_id, TransformationRow.source_field == draft.source_field))
        row = TransformationRow(feed_id=feed_id, source_field=draft.source_field, version=(latest or 0) + 1,
                                steps=steps_json, proposal_id=proposal_id, created_by=created_by)
        self._session.add(row)
        self._session.flush()
        return Transformation.model_validate(row)

    def current_for_feed(self, feed_id: UUID) -> list[Transformation]:
        """The highest version of every source field of the feed that is mapped, by source field."""
        mapped = exists().where(MappingRow.feed_id == TransformationRow.feed_id,
                                MappingRow.source_field == TransformationRow.source_field)
        query = (
            select(TransformationRow)
            .where(TransformationRow.feed_id == feed_id, mapped)
            .ext(distinct_on(TransformationRow.source_field))
            .order_by(TransformationRow.source_field, TransformationRow.version.desc())
        )
        return [Transformation.model_validate(r) for r in self._session.scalars(query)]

    def history(self, feed_id: UUID, source_field: str) -> list[Transformation]:
        query = (
            select(TransformationRow)
            .where(TransformationRow.feed_id == feed_id, TransformationRow.source_field == source_field)
            .order_by(TransformationRow.version)
        )
        return [Transformation.model_validate(r) for r in self._session.scalars(query)]


class KeyPolicyRepository:
    def __init__(self, session: Session):
        self._session = session

    def set(self, feed_id: UUID, policy: str, key_columns: Sequence[str], set_by: str, reason: str) -> KeyPolicy:
        """The feed's next key policy. Who may set it (data_steward) is the step's check, not this one's."""
        draft = KeyPolicyDraft(policy=policy, key_columns=list(key_columns), reason=reason)
        feed = self._session.get(FeedRow, feed_id)
        if feed is None:
            raise FeedNotFound(f"No feed with id {feed_id}")
        if feed.stage != "registered":
            raise FeedNotRegistered(f"Feed {feed_id} is {feed.stage}, not registered")
        latest = self._session.scalar(select(func.max(FeedKeyPolicyRow.version)).where(
            FeedKeyPolicyRow.feed_id == feed_id))
        row = FeedKeyPolicyRow(feed_id=feed_id, version=(latest or 0) + 1, policy=draft.policy,
                               key_columns=draft.key_columns, set_by=set_by, reason=draft.reason)
        self._session.add(row)
        self._session.flush()
        return KeyPolicy.model_validate(row)

    def current(self, feed_id: UUID) -> KeyPolicy | None:
        """The latest policy; None means the default (flag, with the key columns of the approved profile summary),
        which the step resolves."""
        row = self._session.scalar(select(FeedKeyPolicyRow).where(FeedKeyPolicyRow.feed_id == feed_id)
                                   .order_by(FeedKeyPolicyRow.version.desc()).limit(1))
        return None if row is None else KeyPolicy.model_validate(row)
