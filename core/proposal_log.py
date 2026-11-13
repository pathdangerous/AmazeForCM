"""ProposalLog (CLAUDE.md rule 4).

Every model call is recorded here before its proposal is shown to a person: agent name, provider,
model and version, prompt id and version, input reference and output. A named person's decision
(approved or rejected) is recorded once and never overwritten.

Both methods flush but do not commit: the caller owns the transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text, Uuid, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from ports.db import Base
from ports.llm.base import LLMResult

Decision = Literal["approved", "rejected"]


class ProposalNotFound(LookupError):
    """No proposal_log row has this id."""


class ProposalAlreadyDecided(Exception):
    """The proposal already has a decision; decisions are never overwritten."""


class ProposalLogEntry(Base):
    __tablename__ = "proposal_log"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status"),
        CheckConstraint(
            "(status = 'pending' AND decided_by IS NULL AND decided_at IS NULL)"
            " OR (status IN ('approved', 'rejected') AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="decision_complete",
        ),
        Index("ix_proposal_log_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    agent_name: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(Text)
    prompt_id: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_ref: Mapped[str] = mapped_column(Text)
    output: Mapped[dict] = mapped_column(JSONB)
    raw_ref: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    decided_by: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)


class ProposalLog:
    def __init__(self, session: Session):
        self._session = session

    def record(self, *, agent_name: str, input_ref: str, result: LLMResult) -> ProposalLogEntry:
        if not agent_name.strip():
            raise ValueError("agent_name is required")
        if not input_ref.strip():
            raise ValueError("input_ref is required")
        entry = ProposalLogEntry(
            agent_name=agent_name,
            provider=result.provider,
            model=result.model,
            model_version=result.model_version,
            prompt_id=result.prompt_id,
            prompt_version=result.prompt_version,
            input_ref=input_ref,
            output=result.output.model_dump(mode="json"),
            raw_ref=result.raw_ref,
            latency_ms=result.latency_ms,
            status="pending",
        )
        self._session.add(entry)
        self._session.flush()
        return entry

    def record_decision(
        self, entry_id: uuid.UUID, *, decision: Decision, decided_by: str, note: str | None = None
    ) -> ProposalLogEntry:
        if decision not in ("approved", "rejected"):
            raise ValueError(f"decision must be 'approved' or 'rejected', not {decision!r}")
        if not decided_by.strip():
            raise ValueError("decided_by must name the person who decided")
        entry = self._session.scalars(
            select(ProposalLogEntry).where(ProposalLogEntry.id == entry_id).with_for_update()
        ).one_or_none()
        if entry is None:
            raise ProposalNotFound(f"No proposal with id {entry_id}")
        if entry.status != "pending":
            raise ProposalAlreadyDecided(
                f"Proposal {entry_id} was already {entry.status} by {entry.decided_by}"
            )
        entry.status = decision
        entry.decided_by = decided_by
        entry.decided_at = datetime.now(UTC)
        entry.decision_note = note
        self._session.flush()
        return entry
