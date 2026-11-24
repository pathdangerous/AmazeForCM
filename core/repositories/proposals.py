"""ProposalRepository: proposals and the approvals that decide them.

Decision rules (phase 1 plan, assumption 8):
- the roles in required_approver_roles decide in that order, one decision per step;
- the same person cannot decide two steps of one proposal, unless REQUIRE_FOUR_EYES is off (phase 7.8; the
  database says, core/repositories/settings.py);
- the first rejection ends the proposal; nothing can be decided after approval or rejection.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from core.domain.proposal import (
    EDITABLE_KINDS,
    Approval,
    Decision,
    Proposal,
    ProposalDraft,
    ProposalKind,
    ProposalStatus,
)
from core.repositories.errors import (
    NotAnApprover,
    ProposalAlreadyDecided,
    ProposalEditRefused,
    ProposalLogEntryNotFound,
    ProposalNotApproved,
    ProposalNotFound,
    SameApproverTwice,
    WrongProposalKind,
)
from core.repositories.settings import FourEyesRepository
from core.repositories.tables import ApprovalRow, ProposalBaseRow, ProposalLogEntry, ProposalRow
from ports.identity.base import User


class ProposalRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, *, proposal_log_id: UUID, **fields: Any) -> Proposal:
        draft = ProposalDraft(**fields)
        if self._session.get(ProposalLogEntry, proposal_log_id) is None:
            raise ProposalLogEntryNotFound(f"No logged model call with id {proposal_log_id}")
        row = ProposalRow(proposal_log_id=proposal_log_id, status="pending", **draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return Proposal.model_validate(row)

    def add_bases(self, proposal_id: UUID, base_ids: list[UUID], *, feed_id: UUID) -> None:
        """The upstream drafts a draft was built on (0032, phase 7.13); written with the draft, in its transaction."""
        for base_id in dict.fromkeys(base_ids):
            self._session.add(ProposalBaseRow(proposal_id=proposal_id, base_id=base_id, feed_id=feed_id))
        self._session.flush()

    def bases(self, proposal_id: UUID) -> list[UUID]:
        """The drafts this one was built on, in the order they were written."""
        return list(self._session.scalars(select(ProposalBaseRow.base_id).where(ProposalBaseRow.proposal_id == proposal_id)
                                          .order_by(ProposalBaseRow.created_at, ProposalBaseRow.base_id)))

    def create_manual(self, *, kind: ProposalKind, payload: dict[str, Any], required_approver_roles: list[str],
                      feed_id: UUID, author: str) -> Proposal:
        """A proposal made by a person (migration 0028): pending, autonomy A1, `author` set, and NO logged call: no model
        was called, and a made-up proposal_log row would be a false record (rule 4). The database refuses a manual item
        with a log row or without an author, and any kind but the four a person may add."""
        draft = ProposalDraft(kind=kind, payload=payload, autonomy="A1", required_approver_roles=required_approver_roles,
                              feed_id=feed_id)
        row = ProposalRow(proposal_log_id=None, origin="manual", author=author, status="pending", **draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return Proposal.model_validate(row)

    def create_by_rule(self, *, kind: ProposalKind, payload: dict[str, Any], required_approver_roles: list[str],
                       feed_id: UUID) -> Proposal:
        """A proposal made by code (migration 0031): origin `rule`, no logged call (no model was called) and no author.
        Only a profile_action; a person still decides it (rule 1)."""
        draft = ProposalDraft(kind=kind, payload=payload, autonomy="A1", required_approver_roles=required_approver_roles,
                              feed_id=feed_id)
        row = ProposalRow(proposal_log_id=None, origin="rule", author=None, status="pending", **draft.model_dump())
        self._session.add(row)
        self._session.flush()
        return Proposal.model_validate(row)

    def get(self, proposal_id: UUID) -> Proposal:
        return Proposal.model_validate(self._row(proposal_id))

    def lock(self, proposal_id: UUID) -> Proposal:
        """The proposal, its row locked until the transaction ends and read fresh (phase 5 lock order: after
        the feed row, before the glossary lock)."""
        return Proposal.model_validate(self._row(proposal_id, lock=True))

    def list(self, status: ProposalStatus | None = None, kind: ProposalKind | None = None) -> list[Proposal]:
        query = select(ProposalRow).order_by(ProposalRow.created_at.desc(), ProposalRow.id)
        if status is not None:
            query = query.where(ProposalRow.status == status)
        if kind is not None:
            query = query.where(ProposalRow.kind == kind)
        return [Proposal.model_validate(row) for row in self._session.scalars(query)]

    def list_for_requirement(self, requirement_id: UUID, kind: ProposalKind,
                             status: ProposalStatus | None = None) -> list[Proposal]:
        """This requirement's proposals of one kind, oldest first."""
        query = (select(ProposalRow)
                 .where(ProposalRow.requirement_id == requirement_id, ProposalRow.kind == kind)
                 .order_by(ProposalRow.created_at, ProposalRow.id))
        if status is not None:
            query = query.where(ProposalRow.status == status)
        return [Proposal.model_validate(row) for row in self._session.scalars(query)]

    def list_for_feed(self, feed_id: UUID, kind: ProposalKind, status: ProposalStatus | None = None) -> list[Proposal]:
        """This feed's proposals of one kind, oldest first."""
        query = (select(ProposalRow).where(ProposalRow.feed_id == feed_id, ProposalRow.kind == kind)
                 .order_by(ProposalRow.created_at, ProposalRow.id))
        if status is not None:
            query = query.where(ProposalRow.status == status)
        return [Proposal.model_validate(row) for row in self._session.scalars(query)]

    def list_for_feed_column(self, kind: ProposalKind, feed_id: UUID, source_field: str,
                             status: ProposalStatus | None = None) -> list[Proposal]:
        """This feed's proposals of one kind for one column (the payload's source_field), oldest first."""
        query = (select(ProposalRow)
                 .where(ProposalRow.kind == kind, ProposalRow.feed_id == feed_id,
                        ProposalRow.payload["source_field"].astext == source_field)
                 .order_by(ProposalRow.created_at, ProposalRow.id))
        if status is not None:
            query = query.where(ProposalRow.status == status)
        return [Proposal.model_validate(row) for row in self._session.scalars(query)]

    def pending_for_column(self, kind: ProposalKind, feed_id: UUID, source_field: str) -> Proposal | None:
        """The one pending proposal of this kind for this column (uq_proposals_one_pending_per_feed_column)."""
        found = self.list_for_feed_column(kind, feed_id, source_field, status="pending")
        return found[0] if found else None

    def edit(self, proposal_id: UUID, *, payload: dict[str, Any], user: User) -> Proposal:
        """Edit a pending proposal before it is decided (phase 7.8 plan, assumption 36; migration 0024): a NEW pending
        proposal with the edited payload, on the same logged call, and the old one becomes superseded, both in one
        statement (amaze_edit_proposal). Only a person who could decide it may edit it. The database checks what an
        edit may change (the column, a mapping's candidates, a glossary option's kind and key stay); a refusal is
        ProposalEditRefused and changes nothing. Editing is not deciding: nobody's approval moves, and the new proposal
        needs every approval step again. The caller owns the transaction and has locked what its step locks."""
        row = self._row(proposal_id, lock=True)
        if row.status != "pending":
            raise ProposalAlreadyDecided(f"Proposal {proposal_id} is already {row.status}")
        if row.kind not in EDITABLE_KINDS:
            raise WrongProposalKind(f"A {row.kind} proposal cannot be edited")
        if not set(row.required_approver_roles) & set(user.roles):
            raise NotAnApprover(f"Proposal {proposal_id} needs one of {row.required_approver_roles}; "
                                f"{user.id} has {user.roles}")
        try:
            with self._session.begin_nested():
                new_id = self._session.execute(
                    text("SELECT amaze_edit_proposal(:old, CAST(:payload AS jsonb), :by)"),
                    {"old": proposal_id, "payload": json.dumps(payload), "by": user.id}).scalar_one()
        except DBAPIError as error:
            state = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
            if state == "23514":  # check_violation: the edit guard's refusals
                raise ProposalEditRefused(str(error.orig).splitlines()[0]) from error
            raise
        self._session.refresh(row)  # the function moved the old row to superseded behind the session's back
        return Proposal.model_validate(self._row(new_id))

    def lineage(self, proposal_id: UUID) -> list[Proposal]:
        """The proposal's edit chain, oldest first: the one the model proposed, then each edit of it, to the last. A
        proposal that was never edited is a chain of one."""
        row = self._row(proposal_id)
        chain = [row]
        while chain[0].edited_from is not None:
            chain.insert(0, self._row(chain[0].edited_from))
        while True:
            child = self._session.scalars(select(ProposalRow).where(ProposalRow.edited_from == chain[-1].id)).one_or_none()
            if child is None:
                break
            chain.append(child)
        return [Proposal.model_validate(r) for r in chain]

    def approvals(self, proposal_id: UUID) -> list[Approval]:
        rows = self._session.scalars(
            select(ApprovalRow).where(ApprovalRow.proposal_id == proposal_id).order_by(ApprovalRow.step)
        )
        return [Approval.model_validate(row) for row in rows]

    def record_decision(
        self, proposal_id: UUID, *, user: User, decision: Decision, note: str | None = None
    ) -> Approval:
        if decision not in ("approved", "rejected"):
            raise ValueError(f"decision must be 'approved' or 'rejected', not {decision!r}")
        row = self._row(proposal_id, lock=True)
        if row.status != "pending":
            raise ProposalAlreadyDecided(f"Proposal {proposal_id} is already {row.status}")
        done = self.approvals(proposal_id)
        step = len(done) + 1
        role = row.required_approver_roles[step - 1]
        if role not in user.roles:
            raise NotAnApprover(f"Step {step} of proposal {proposal_id} needs role {role}; {user.id} has {user.roles}")
        if any(a.decided_by == user.id for a in done) and FourEyesRepository(self._session).current():
            raise SameApproverTwice(f"{user.id} already decided a step of proposal {proposal_id}")

        approval = ApprovalRow(
            proposal_id=proposal_id, step=step, role=role, decided_by=user.id, decision=decision, note=note
        )
        self._session.add(approval)
        if decision == "rejected":
            row.status, row.decided_at = "rejected", datetime.now(UTC)
        elif step == len(row.required_approver_roles):
            row.status, row.decided_at = "approved", datetime.now(UTC)
        self._session.flush()
        return Approval.model_validate(approval)

    def approve_my_steps(self, proposal_id: UUID, *, user: User, note: str | None = None) -> list[Approval]:
        """Approve the next step and, when REQUIRE_FOUR_EYES is off, every following step whose role the user
        holds (each its own approvals row, so the audit shows every role), stopping at the first role the user
        lacks. When it is on, exactly one step: the same as record_decision(..., "approved")."""
        done = [self.record_decision(proposal_id, user=user, decision="approved", note=note)]
        if FourEyesRepository(self._session).current():
            return done
        while True:
            row = self._row(proposal_id)
            if row.status != "pending" or row.required_approver_roles[len(self.approvals(proposal_id))] not in user.roles:
                return done
            done.append(self.record_decision(proposal_id, user=user, decision="approved", note=note))

    def require_approved(self, proposal_id: UUID, *, kind: ProposalKind) -> Proposal:
        """The proposal, if it is approved and of this kind; used before writing configuration."""
        proposal = self.get(proposal_id)
        if proposal.status != "approved":
            raise ProposalNotApproved(f"Proposal {proposal_id} is {proposal.status}, not approved")
        if proposal.kind != kind:
            raise WrongProposalKind(f"Proposal {proposal_id} is a {proposal.kind} proposal, not {kind}")
        return proposal

    def audit(self, proposal_id: UUID) -> dict[str, Any]:
        """Rule 4 in one place: the logged model call, the proposal and every decision (view proposal_audit)."""
        row = self._session.execute(
            text("SELECT * FROM proposal_audit WHERE proposal_id = :id"), {"id": proposal_id}
        ).mappings().one_or_none()
        if row is None:
            raise ProposalNotFound(f"No proposal with id {proposal_id}")
        return dict(row)

    def _row(self, proposal_id: UUID, *, lock: bool = False) -> ProposalRow:
        query = select(ProposalRow).where(ProposalRow.id == proposal_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)  # fresh after the lock
        row = self._session.scalars(query).one_or_none()
        if row is None:
            raise ProposalNotFound(f"No proposal with id {proposal_id}")
        return row
