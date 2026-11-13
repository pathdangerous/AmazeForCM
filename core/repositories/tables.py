"""Every domain table. Kept free of ports.llm and agents so the run engine can load it (rule 2).

Constraint names follow ports.db.base.NAMING_CONVENTION; the migrations use the same full names.
Append-only tables are protected by a database trigger (migration 0002), not by this code.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.feed import FEED_STAGES
from core.domain.glossary import DATA_TYPES
from core.domain.proposal import PROPOSAL_KINDS
from core.domain.requirement import REQUIREMENT_STATUSES
from ports.db import Base
from ports.identity.base import ROLES


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))


def _created_at(*, clock: bool = False) -> Mapped[datetime]:
    """now() is the transaction start, the same for every row of one transaction. Tables listed
    "newest first" use clock_timestamp() (the moment of the insert) so their order is stable."""
    return mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp() if clock else func.now())


class ProposalLogEntry(Base):
    """One model call (rule 4). Append-only."""

    __tablename__ = "proposal_log"

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at()
    agent_name: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(Text)
    prompt_id: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_ref: Mapped[str] = mapped_column(Text)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB)
    raw_ref: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer)


class ProposalRow(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint(_in("kind", PROPOSAL_KINDS), name="kind"),
        CheckConstraint("autonomy IN ('A1', 'A2')", name="autonomy"),
        CheckConstraint("cardinality(required_approver_roles) >= 1", name="has_approver"),
        CheckConstraint(
            f"required_approver_roles <@ ARRAY[{', '.join(repr(r) for r in ROLES)}]::text[]", name="known_roles"
        ),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status"),
        CheckConstraint("(status = 'pending') = (decided_at IS NULL)", name="decided_at_matches_status"),
        Index("ix_proposals_status", "status"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)  # default changed in migration 0004
    proposal_log_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposal_log.id"))
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    autonomy: Mapped[str] = mapped_column(Text)
    required_approver_roles: Mapped[list[str]] = mapped_column(ARRAY(Text))
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requirements.id"))
    feed_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("feeds.id"))


class ApprovalRow(Base):
    """One named person's decision on one step of a proposal. Append-only."""

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("proposal_id", "step"),
        UniqueConstraint("proposal_id", "decided_by"),
        CheckConstraint("step >= 1", name="step"),
        CheckConstraint(_in("role", ROLES), name="role"),
        CheckConstraint("length(btrim(decided_by)) > 0", name="decided_by"),
        CheckConstraint("decision IN ('approved', 'rejected')", name="decision"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at()
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"))
    step: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


class GlossaryElementRow(Base):
    """One version of one glossary element. Append-only; the current version is the highest."""

    __tablename__ = "glossary_elements"
    __table_args__ = (
        UniqueConstraint("key", "version"),
        CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name="key_format"),
        CheckConstraint("domain ~ '^[a-z][a-z0-9_]*$'", name="domain_format"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(_in("data_type", DATA_TYPES), name="data_type"),
        CheckConstraint("length(btrim(name)) > 0 AND length(btrim(definition)) > 0", name="text_present"),
        # Rule 1: only the seed may create an element without an approved proposal.
        CheckConstraint("(proposal_id IS NULL) = (created_by = 'seed')", name="proposal_unless_seed"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at()
    key: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    data_type: Mapped[str] = mapped_column(Text)
    definition: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default=text("'{}'::text[]"))
    created_by: Mapped[str] = mapped_column(Text)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proposals.id"))


class RequirementRow(Base):
    __tablename__ = "requirements"
    __table_args__ = (
        CheckConstraint(_in("status", REQUIREMENT_STATUSES), name="status"),
        CheckConstraint("length(btrim(requester_id)) > 0 AND length(btrim(title)) > 0", name="text_present"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    requester_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(Text, default="defined", server_default="defined")


class FeedRow(Base):
    __tablename__ = "feeds"
    __table_args__ = (
        UniqueConstraint("code"),
        CheckConstraint("code ~ '^[a-z][a-z0-9_]*$'", name="code_format"),
        CheckConstraint(_in("stage", FEED_STAGES), name="stage"),
        # Step 04 outcome no_owner: a registered feed must have an owner.
        CheckConstraint("stage <> 'registered' OR owner_id IS NOT NULL", name="registered_has_owner"),
        CheckConstraint("(stage = 'registered') = (registered_at IS NOT NULL)", name="registered_at_matches_stage"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text, default="candidate", server_default="candidate")
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requirements.id"))
    owner_id: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
