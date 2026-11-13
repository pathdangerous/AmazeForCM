"""ProposalRepository: proposals and the approvals that decide them.

Decision rules (phase 1 plan, assumption 8):
- the roles in required_approver_roles decide in that order, one decision per step;
- the same person cannot decide two steps of one proposal;
- the first rejection ends the proposal; nothing can be decided after approval or rejection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.domain.proposal import Approval, Decision, Proposal, ProposalDraft, ProposalKind, ProposalStatus
from core.repositories.errors import (
    NotAnApprover,
    ProposalAlreadyDecided,
    ProposalLogEntryNotFound,
    ProposalNotApproved,
    ProposalNotFound,
    SameApproverTwice,
    WrongProposalKind,
)
from core.repositories.tables import ApprovalRow, ProposalLogEntry, ProposalRow
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

    def get(self, proposal_id: UUID) -> Proposal:
        return Proposal.model_validate(self._row(proposal_id))

    def list(self, status: ProposalStatus | None = None, kind: ProposalKind | None = None) -> list[Proposal]:
        query = select(ProposalRow).order_by(ProposalRow.created_at.desc(), ProposalRow.id)
        if status is not None:
            query = query.where(ProposalRow.status == status)
        if kind is not None:
            query = query.where(ProposalRow.kind == kind)
        return [Proposal.model_validate(row) for row in self._session.scalars(query)]

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
        if any(a.decided_by == user.id for a in done):
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
            query = query.with_for_update()
        row = self._session.scalars(query).one_or_none()
        if row is None:
            raise ProposalNotFound(f"No proposal with id {proposal_id}")
        return row
