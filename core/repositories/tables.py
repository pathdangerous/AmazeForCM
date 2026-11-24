"""Every domain table. Kept free of ports.llm and agents so the run engine can load it (rule 2).

Constraint names follow ports.db.base.NAMING_CONVENTION; the migrations use the same full names.
Append-only tables are protected by a database trigger (migration 0002), not by this code.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.consumer_request import ROUTING_OUTCOMES
from core.domain.feed import CONNECTORS, FEED_STAGES
from core.domain.glossary import DATA_TYPES
from core.domain.load import ARRIVAL_OUTCOMES, CERTIFICATION_OUTCOMES, LOAD_STATUSES
from core.domain.mapping import GLOSSARY_RUN_REASONS, GLOSSARY_RUN_STATUSES
from core.domain.onboarding import EVENT_NAMES, JOB_STATUSES, ONBOARDING_STEPS, RUN_MODES, SESSION_STATUSES
from core.domain.product import VISIBILITIES
from core.domain.proposal import PROPOSAL_KINDS
from core.domain.requirement import REQUIREMENT_STATUSES
from core.domain.sourcing import CHOICE_ACTIONS, CHOICE_BASES, FETCH_OUTCOMES, RELEASE_REASONS, SCOPE_VISIBILITIES
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
    """One model call (rule 4): exactly one row per answered call, committed right after the call.
    Append-only. A discarded call also has a ProposalLogDiscardRow (0012)."""

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


class ProposalLogDiscardRow(Base):
    """A model call whose answer was discarded: the status changed during the call, or writing its input
    or proposals failed. One per call at most; the call can have no proposal. Append-only (0012)."""

    __tablename__ = "proposal_log_discards"
    __table_args__ = (
        UniqueConstraint("proposal_log_id"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    proposal_log_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposal_log.id"))
    reason: Mapped[str] = mapped_column(Text)


class ProposalRow(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint(_in("kind", PROPOSAL_KINDS), name="kind"),
        CheckConstraint("autonomy IN ('A1', 'A2')", name="autonomy"),
        CheckConstraint("cardinality(required_approver_roles) >= 1", name="has_approver"),
        CheckConstraint(
            f"required_approver_roles <@ ARRAY[{', '.join(repr(r) for r in ROLES)}]::text[]", name="known_roles"
        ),
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'superseded')", name="status"),  # 0024
        CheckConstraint("(status = 'pending') = (decided_at IS NULL)", name="decided_at_matches_status"),
        Index("ix_proposals_status", "status"),
        # 0024: an edit is a new row that names the row it replaces and who edited it, both or neither
        CheckConstraint("(edited_from IS NULL) = (edited_by IS NULL)", name="edited_from_and_by_together"),
        CheckConstraint("edited_by IS NULL OR length(btrim(edited_by)) > 0", name="edited_by"),
        Index("uq_proposals_edited_from", "edited_from", unique=True, postgresql_where=text("edited_from IS NOT NULL")),
        # 0016, 0017: these kinds have a feed and name their column (a non-blank JSON string); one pending
        # proposal of them per feed and column
        CheckConstraint("kind NOT IN ('glossary_element', 'mapping') "
                        "OR (feed_id IS NOT NULL "
                        "AND COALESCE(jsonb_typeof(payload -> 'source_field') = 'string', false) "
                        "AND length(btrim(payload ->> 'source_field')) > 0)", name="names_a_column"),
        Index("uq_proposals_one_pending_per_feed_column", "kind", "feed_id", text("(payload ->> 'source_field')"),
              unique=True, postgresql_where=text("status = 'pending' AND kind IN ('glossary_element', 'mapping')")),
        # 0018 (G1, G2): a rule proposal has a feed and names its rule_key and sql; one pending rule proposal per
        # feed and rule key, one pending review_summary per feed
        CheckConstraint("kind <> 'rule' "
                        "OR (feed_id IS NOT NULL "
                        "AND COALESCE(jsonb_typeof(payload -> 'rule_key') = 'string', false) "
                        "AND length(btrim(payload ->> 'rule_key')) > 0 "
                        "AND COALESCE(jsonb_typeof(payload -> 'sql') = 'string', false) "
                        "AND length(btrim(payload ->> 'sql')) > 0)", name="names_a_rule"),
        Index("uq_proposals_one_pending_rule_per_feed_key", "feed_id", text("(payload ->> 'rule_key')"),
              unique=True, postgresql_where=text("status = 'pending' AND kind = 'rule'")),
        Index("uq_proposals_one_pending_review_summary_per_feed", "feed_id", unique=True,
              postgresql_where=text("status = 'pending' AND kind = 'review_summary'")),
        # 0023: a transformation proposal has a feed and names its column; one pending per feed and column
        CheckConstraint("kind <> 'transformation' "
                        "OR (feed_id IS NOT NULL "
                        "AND COALESCE(jsonb_typeof(payload -> 'source_field') = 'string', false) "
                        "AND length(btrim(payload ->> 'source_field')) > 0)", name="names_a_transformation"),
        Index("uq_proposals_one_pending_transformation_per_feed_column", "feed_id",
              text("(payload ->> 'source_field')"), unique=True,
              postgresql_where=text("status = 'pending' AND kind = 'transformation'")),
        # 0034 (phase 7.14): one pending exception fix per feed, load and exception group
        Index("uq_proposals_one_pending_exception_fix", "feed_id", text("(payload ->> 'load_id')"),
              text("(payload ->> 'group_key')"), unique=True,
              postgresql_where=text("status = 'pending' AND kind = 'exception_fix'")),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)  # default changed in migration 0004
    proposal_log_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proposal_log.id"))  # 0028: NULL when manual
    origin: Mapped[str] = mapped_column(Text, default="agent", server_default="agent")  # 0028: agent | manual
    author: Mapped[str | None] = mapped_column(Text)  # 0028: who made a manual item
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    autonomy: Mapped[str] = mapped_column(Text)
    required_approver_roles: Mapped[list[str]] = mapped_column(ARRAY(Text))
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requirements.id"))
    # 0027: no FK. A proposal a glossary element was made from is kept when its feed is deleted and still names it
    # (feed_deletions records the feed); every other proposal of the feed is deleted with it.
    feed_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    edited_from: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proposals.id"))  # 0024
    edited_by: Mapped[str | None] = mapped_column(Text)  # 0024


class ApprovalRow(Base):
    """One named person's decision on one step of a proposal. Append-only."""

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("proposal_id", "step"),
        # one person, one step: from 0020 a trigger reads amaze_settings (amaze_approvals_four_eyes), not a constraint
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
        CheckConstraint("unit IS NULL OR length(btrim(unit)) > 0", name="unit"),  # 0025
        # Rule 1: only the seed may create an element without an approved proposal.
        CheckConstraint("(proposal_id IS NULL) = (created_by = 'seed')", name="proposal_unless_seed"),
        Index("uq_glossary_elements_proposal_id", "proposal_id", unique=True,
              postgresql_where=text("proposal_id IS NOT NULL")),  # 0016: one row per proposal
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
    unit: Mapped[str | None] = mapped_column(Text)  # 0025
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


class RequirementElementRow(Base):
    """One glossary element a requirement asks for. Append-only.

    A database trigger (migration 0006) refuses an element_key that has no row in glossary_elements.
    """

    __tablename__ = "requirement_elements"
    __table_args__ = (UniqueConstraint("requirement_id", "element_key"),)

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirements.id"))
    element_key: Mapped[str] = mapped_column(Text)


class RequirementTermRow(Base):
    """One term the requester ticked that is not in the glossary (element_not_in_glossary). Append-only."""

    __tablename__ = "requirement_terms"
    __table_args__ = (
        UniqueConstraint("requirement_id", "term"),
        CheckConstraint("length(btrim(term)) > 0", name="term_present"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirements.id"))
    term: Mapped[str] = mapped_column(Text)


class FeedRow(Base):
    __tablename__ = "feeds"
    __table_args__ = (
        UniqueConstraint("code"),
        CheckConstraint("code ~ '^[a-z][a-z0-9_]*$'", name="code_format"),
        CheckConstraint(_in("stage", FEED_STAGES), name="stage"),
        # Step 04 outcome no_owner: a registered feed must have an owner (0027: a paused feed is a registered one)
        CheckConstraint("stage NOT IN ('registered', 'paused') OR owner_id IS NOT NULL", name="registered_has_owner"),
        CheckConstraint("(stage IN ('registered', 'paused')) = (registered_at IS NOT NULL)",
                        name="registered_at_matches_stage"),
        # migration 0008: the source a feed reads; one feed per source
        UniqueConstraint("source_code"),
        CheckConstraint(_in("connector", CONNECTORS), name="connector"),
        CheckConstraint("(source_code IS NULL) = (connector IS NULL)", name="source_and_connector_together"),
        CheckConstraint("connector IS NULL OR source_ref IS NOT NULL", name="connector_has_source_ref"),
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
    source_code: Mapped[str | None] = mapped_column(Text)
    connector: Mapped[str | None] = mapped_column(Text)


class FeedStatusEventRow(Base):
    """A pause or a resume of a feed, with the reason and the person (migration 0027). Append-only; the stage guard
    allows registered <-> paused only right after the matching row."""

    __tablename__ = "feed_status_events"
    __table_args__ = (
        CheckConstraint("action IN ('pause', 'resume')", name="action"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason"),
        CheckConstraint('length(btrim("by")) > 0', name="by"),
        Index("ix_feed_status_events_feed_id", "feed_id"),
    )

    id: Mapped[uuid.UUID] = _id()
    at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    action: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    by: Mapped[str] = mapped_column("by", Text)


class FeedDeletionRow(Base):
    """The record of a hard-deleted feed: who, when, why, and how many rows of each table went (migration 0027).
    Append-only; written only by amaze_delete_feed(). No FK: it outlives the feed."""

    __tablename__ = "feed_deletions"
    __table_args__ = (
        CheckConstraint("length(btrim(reason)) > 0", name="reason"),
        CheckConstraint('length(btrim("by")) > 0', name="by"),
    )

    id: Mapped[uuid.UUID] = _id()
    at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    by: Mapped[str] = mapped_column("by", Text)
    reason: Mapped[str] = mapped_column(Text)
    rows_deleted: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ProfileErasureRow(Base):
    """The record of an erase of profiling history: who, when, why, which profiles went (ids and versions) and which were
    kept with the reason (migration 0036). Append-only; written only by amaze_erase_profiles(). No FK: it outlives the
    feed, as feed_deletions does."""

    __tablename__ = "profile_erasures"
    __table_args__ = (
        CheckConstraint("length(btrim(reason)) > 0", name="reason"),
        CheckConstraint('length(btrim("by")) > 0', name="by"),
        CheckConstraint("erased_count >= 1 AND erased_count = cardinality(erased_ids) "
                        "AND erased_count = cardinality(erased_versions)", name="erased_count_matches"),
        CheckConstraint("jsonb_typeof(kept) = 'array' AND kept_count = jsonb_array_length(kept)", name="kept_count_matches"),
        Index("ix_profile_erasures_feed_id", "feed_id"),
    )

    id: Mapped[uuid.UUID] = _id()
    at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    feed_code: Mapped[str] = mapped_column(Text)
    before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    by: Mapped[str] = mapped_column("by", Text)
    reason: Mapped[str] = mapped_column(Text)
    erased_count: Mapped[int] = mapped_column(Integer)
    erased_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    erased_versions: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    kept: Mapped[list[Any]] = mapped_column(JSONB)
    kept_count: Mapped[int] = mapped_column(Integer)


class SourceChoiceRow(Base):
    """Which source, and so which candidate feed, a requirement was handed to: one row per version, a
    selection or a release; the highest version is current. Append-only (migrations 0008 and 0009).

    The database also enforces the versions, the order of selections and releases, and the pick's
    approved proposal (triggers in 0009; phase 3 plan, assumption 50)."""

    __tablename__ = "source_choices"
    __table_args__ = (
        UniqueConstraint("requirement_id", "version"),
        Index("uq_source_choices_selected_source", "requirement_id", "source_code", unique=True,
              postgresql_where=text("action = 'selected'")),
        CheckConstraint(_in("basis", CHOICE_BASES), name="basis"),
        CheckConstraint(_in("action", CHOICE_ACTIONS), name="action"),
        CheckConstraint(_in("reason", RELEASE_REASONS), name="reason"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "(action = 'selected' AND basis IS NOT NULL AND reason IS NULL) OR "
            "(action = 'released' AND reason IS NOT NULL AND basis IS NULL)",
            name="selection_or_release",
        ),
        CheckConstraint(
            "(COALESCE(basis, '') = 'requester_pick') = (proposal_id IS NOT NULL) AND "
            "(COALESCE(basis, '') = 'requester_pick') = (accepted_conditions IS NOT NULL)",
            name="pick_has_its_acceptance",
        ),
        CheckConstraint(
            "(decided_by IS NOT NULL) = "
            "(COALESCE(basis, '') = 'requester_pick' OR COALESCE(reason, '') = 'requester_asked')",
            name="decided_by_only_for_a_person",
        ),
        CheckConstraint(
            "accepted_conditions IS NULL OR jsonb_typeof(accepted_conditions) = 'object'",
            name="conditions_are_an_object",
        ),
        Index("ix_source_choices_feed_id", "feed_id"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirements.id"))
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    source_code: Mapped[str] = mapped_column(Text)
    basis: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proposals.id"))
    accepted_conditions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decided_by: Mapped[str | None] = mapped_column(Text)


class RequirementScopeRow(Base):
    """S1: the scope the requester confirmed, versioned per requirement. Append-only (migration 0009).

    Triggers: an approved scope proposal of the same requirement, holding exactly this scope; confirmed
    by the requirement's own requester."""

    __tablename__ = "requirement_scopes"
    __table_args__ = (
        UniqueConstraint("requirement_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(_in("visibility", SCOPE_VISIBILITIES), name="visibility"),
        CheckConstraint("length(btrim(provider)) > 0", name="provider"),
        CheckConstraint("cardinality(where_to_search) >= 1", name="where_to_search"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirements.id"))
    version: Mapped[int] = mapped_column(Integer)
    visibility: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    where_to_search: Mapped[list[str]] = mapped_column(ARRAY(Text))
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"))
    confirmed_by: Mapped[str] = mapped_column(Text)


class SourceFetchRow(Base):
    """One fetch of a feed's source: the stored file, or why the source did not answer.
    Append-only (migration 0008)."""

    __tablename__ = "source_fetches"
    __table_args__ = (
        CheckConstraint(_in("outcome", FETCH_OUTCOMES), name="outcome"),
        CheckConstraint(
            # IS NOT NULL is spelt out: a check that evaluates to NULL passes.
            "outcome <> 'fetched' OR (blob_ref IS NOT NULL AND size_bytes IS NOT NULL AND size_bytes > 0 "
            "AND detail IS NULL)",
            name="fetched_has_a_file",
        ),
        CheckConstraint(
            "outcome <> 'source_down' OR (detail IS NOT NULL AND length(btrim(detail)) > 0 "
            "AND blob_ref IS NULL AND size_bytes IS NULL)",
            name="down_has_a_reason",
        ),
        Index("ix_source_fetches_feed_id", "feed_id"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    outcome: Mapped[str] = mapped_column(Text)
    blob_ref: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    detail: Mapped[str | None] = mapped_column(Text)


class FeedProfileRow(Base):
    """Step 02: one DuckDB profile of a feed's fetched file, versioned per feed; the highest version is
    current. Append-only (migration 0014; triggers: the next version, a fetched file of the same feed)."""

    __tablename__ = "feed_profiles"
    __table_args__ = (
        UniqueConstraint("feed_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("outcome IN ('profiled', 'spec_only_low_confidence')", name="outcome"),
        CheckConstraint(
            "outcome <> 'profiled' OR (row_count IS NOT NULL AND row_count >= 1 AND detail IS NULL "
            "AND jsonb_typeof(columns) = 'array' AND jsonb_array_length(columns) >= 1)", name="profiled_has_rows"),
        CheckConstraint(
            "outcome <> 'spec_only_low_confidence' OR (COALESCE(row_count, 0) = 0 AND detail IS NOT NULL "
            "AND length(btrim(detail)) > 0)", name="spec_only_has_a_reason"),
        CheckConstraint(
            "jsonb_typeof(columns) = 'array' AND jsonb_typeof(coverage) = 'array' AND jsonb_typeof(spec) = 'object'",
            name="json_shapes"),
        CheckConstraint("length(btrim(engine)) > 0 AND length(btrim(profiled_by)) > 0", name="text_present"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    version: Mapped[int] = mapped_column(Integer)
    fetch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_fetches.id"))
    outcome: Mapped[str] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(Integer)
    columns: Mapped[list[Any]] = mapped_column(JSONB)
    coverage: Mapped[list[Any]] = mapped_column(JSONB)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    detail: Mapped[str | None] = mapped_column(Text)
    engine: Mapped[str] = mapped_column(Text)
    profiled_by: Mapped[str] = mapped_column(Text)


class FeedSignoffRow(Base):
    """Step 03: the backend team's sign-off of a profile, versioned per feed; the gaps and reasons are kept.
    Append-only (migration 0014; triggers: the next version; a candidate feed, its latest profile, that
    profile's summary decided by the signer; decision D1)."""

    __tablename__ = "feed_signoffs"
    __table_args__ = (
        UniqueConstraint("feed_id", "version"),
        UniqueConstraint("profile_id"),
        UniqueConstraint("proposal_id"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("outcome IN ('meets', 'meets_with_gaps', 'rejected')", name="outcome"),
        CheckConstraint(
            "(outcome = 'meets' AND cardinality(gaps) = 0 AND cardinality(reasons) = 0) OR "
            "(outcome = 'meets_with_gaps' AND cardinality(gaps) >= 1 AND cardinality(reasons) = 0) OR "
            "(outcome = 'rejected' AND cardinality(reasons) >= 1 AND cardinality(gaps) = 0)",
            name="gaps_and_reasons_fit"),
        CheckConstraint("length(btrim(signed_off_by)) > 0", name="signed_off_by"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    version: Mapped[int] = mapped_column(Integer)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feed_profiles.id"))
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"))
    outcome: Mapped[str] = mapped_column(Text)
    gaps: Mapped[list[str]] = mapped_column(ARRAY(Text))
    reasons: Mapped[list[str]] = mapped_column(ARRAY(Text))
    signed_off_by: Mapped[str] = mapped_column(Text)


class FeedRegistrationRow(Base):
    """Step 04: owner, licence and visibility of a registered feed, versioned per feed. Append-only
    (migration 0014; trigger: the feed's latest sign-off, accepting, on its latest profile)."""

    __tablename__ = "feed_registrations"
    __table_args__ = (
        UniqueConstraint("feed_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("visibility IN ('open', 'restricted')", name="visibility"),
        CheckConstraint(
            "length(btrim(owner_id)) > 0 AND length(btrim(licence)) > 0 AND length(btrim(permitted_use)) > 0 "
            "AND length(btrim(redistribution)) > 0 AND length(btrim(registered_by)) > 0", name="text_present"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    version: Mapped[int] = mapped_column(Integer)
    signoff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feed_signoffs.id"))
    owner_id: Mapped[str] = mapped_column(Text)
    licence: Mapped[str] = mapped_column(Text)
    permitted_use: Mapped[str] = mapped_column(Text)
    redistribution: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(Text)
    registered_by: Mapped[str] = mapped_column(Text)


class GlossaryRunRow(Base):
    """Step 05: one GlossaryBuilderAgent run for one feed (migration 0016). Operational state, not
    append-only: its status moves queued -> running -> done or failed (running again on a new attempt),
    a finished run never changes, DELETE and TRUNCATE are refused. One queued or running run per feed."""

    __tablename__ = "glossary_runs"
    __table_args__ = (
        CheckConstraint(_in("reason", GLOSSARY_RUN_REASONS), name="reason"),
        CheckConstraint(_in("status", GLOSSARY_RUN_STATUSES), name="status"),
        CheckConstraint("attempt >= 0", name="attempt"),
        CheckConstraint("length(btrim(requested_by)) > 0", name="requested_by"),
        CheckConstraint("status <> 'running' OR lease_until IS NOT NULL", name="running_has_a_lease"),
        CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="failed_has_an_error"),
        CheckConstraint("(status = 'done') = (results IS NOT NULL)", name="done_has_results"),
        CheckConstraint("(status IN ('done', 'failed')) = (finished_at IS NOT NULL)",
                        name="finished_at_matches_status"),
        CheckConstraint("proposal_log_id IS NULL OR status = 'done'", name="call_only_when_done"),
        Index("ix_glossary_runs_feed_id", "feed_id"),
        Index("uq_glossary_runs_one_active_per_feed", "feed_id", unique=True,
              postgresql_where=text("status IN ('queued', 'running')")),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feed_profiles.id"))
    reason: Mapped[str] = mapped_column(Text)
    columns: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default=text("'{}'::text[]"))
    requested_by: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="queued", server_default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proposal_log_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proposal_log.id"))
    results: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class MappingRow(Base):
    """Level 1 mapping: one source field to one glossary element. Append-only.

    A database trigger (migration 0004) refuses an element_key that has no row in glossary_elements.
    """

    __tablename__ = "mappings"
    __table_args__ = (
        UniqueConstraint("feed_id", "source_field", "version"),
        CheckConstraint("length(btrim(source_field)) > 0", name="source_field"),
        CheckConstraint("version >= 1", name="version"),
        Index("uq_mappings_proposal_id", "proposal_id", unique=True),  # 0016: one row per proposal
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    source_field: Mapped[str] = mapped_column(Text)
    element_key: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"))


class RuleRow(Base):
    """One SQL validation rule of a feed. Append-only."""

    __tablename__ = "rules"
    __table_args__ = (
        UniqueConstraint("feed_id", "rule_key", "version"),
        CheckConstraint("rule_key ~ '^[a-z][a-z0-9_]*$'", name="rule_key_format"),
        CheckConstraint("length(btrim(sql)) > 0 AND length(btrim(description)) > 0", name="text_present"),
        CheckConstraint("version >= 1", name="version"),
        Index("uq_rules_proposal_id", "proposal_id", unique=True),  # 0018 (G3): one row per proposal
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    rule_key: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    sql: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"))


class TransformationRow(Base):
    """The approved safe steps for one mapped column of a feed (phase 7.8, assumption 28). Append-only. Migration
    0023: a row matches its approved transformation proposal, and the JSON holds only the fixed set of steps."""

    __tablename__ = "transformations"
    __table_args__ = (
        UniqueConstraint("feed_id", "source_field", "version"),
        CheckConstraint("length(btrim(source_field)) > 0", name="source_field"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("length(btrim(created_by)) > 0", name="created_by"),
        CheckConstraint("amaze_valid_transform_steps(steps)", name="steps"),
        Index("uq_transformations_proposal_id", "proposal_id", unique=True),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    source_field: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"))
    created_by: Mapped[str] = mapped_column(Text)


class FeedKeyPolicyRow(Base):
    """What a load does with duplicated key rows, versioned per feed (phase 7.8, assumption 29). Append-only."""

    __tablename__ = "feed_key_policies"
    __table_args__ = (
        UniqueConstraint("feed_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("policy IN ('hold', 'keep_last', 'flag')", name="policy"),
        CheckConstraint("amaze_valid_key_columns(key_columns)", name="key_columns"),
        CheckConstraint("length(btrim(set_by)) > 0", name="set_by"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason"),
    )

    id: Mapped[uuid.UUID] = _id()
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    version: Mapped[int] = mapped_column(Integer)
    policy: Mapped[str] = mapped_column(Text)
    key_columns: Mapped[list[str]] = mapped_column(ARRAY(Text))
    set_by: Mapped[str] = mapped_column(Text)
    set_at: Mapped[datetime] = _created_at(clock=True)
    reason: Mapped[str] = mapped_column(Text)


class LoadRow(Base):
    """One delivery of a registered feed (steps 12 to 15). Operational state: its status moves once, from
    received to loaded or failed_rolled_back, with its proof (migration 0018, trg_loads_a_status); a loaded
    scheduled load may go on to held or released with a certification (0019); a received one may end
    quarantined, held (breaking_held) or abandoned. The arrival fields are set once, while received (0019,
    trg_loads_b_arrival). One received load per feed."""

    __tablename__ = "loads"
    __table_args__ = (
        CheckConstraint(_in("status", LOAD_STATUSES), name="status"),
        CheckConstraint("length(btrim(file_ref)) > 0", name="file_ref"),
        # 0018 (G7): a scheduled load names the go-live that approved it, a test load has none
        CheckConstraint("(purpose = 'scheduled' AND go_live_id IS NOT NULL) "
                        "OR (purpose = 'go_live_test' AND go_live_id IS NULL)", name="purpose_fits"),
        # 0019: a SHA-256 in lower-case hex; one of the four arrival outcomes (NULL: no arrival yet)
        CheckConstraint("content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha256"),
        CheckConstraint(f"arrival_outcome IS NULL OR {_in('arrival_outcome', ARRIVAL_OUTCOMES)}",
                        name="arrival_outcome"),
        Index("ix_loads_feed_id", "feed_id"),
        Index("uq_loads_one_received_per_feed", "feed_id", unique=True, postgresql_where=text("status = 'received'")),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    file_ref: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())
    status: Mapped[str] = mapped_column(Text, default="received", server_default="received")
    purpose: Mapped[str] = mapped_column(Text)
    fetch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_fetches.id"))
    go_live_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("feed_go_lives.id", use_alter=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_stage: Mapped[str | None] = mapped_column(Text)
    failure: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(Text)  # 0019: step 12
    arrival_outcome: Mapped[str | None] = mapped_column(Text)
    arrival_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    arrival_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("feed_profiles.id"))
    # 0034 (phase 7.14): a rerun of a held load's own file, with its reason; set once, at insert (trg_loads_c_rerun)
    rerun_of_load_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("loads.id"))
    rerun_reason: Mapped[str | None] = mapped_column(Text)
    rerun_by: Mapped[str | None] = mapped_column(Text)  # 0035 (phase 7.15): who started it; None for a rerun of 0034


class LoadCertificationRow(Base):
    """Step 15: the trust score of one loaded scheduled load, its threshold and its outcome. Append-only
    (migration 0019; trigger: the database recounts the load's records and flags, the score and the outcome)."""

    __tablename__ = "load_certifications"
    __table_args__ = (
        UniqueConstraint("load_id"),
        CheckConstraint(_in("outcome", CERTIFICATION_OUTCOMES), name="outcome"),
        CheckConstraint("rows >= 0 AND flagged_rows >= 0 AND flagged_rows <= rows", name="counts"),
        CheckConstraint("trust_score >= 0 AND trust_score <= 100 AND threshold >= 1 AND threshold <= 100",
                        name="score"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    load_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("loads.id"))
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    rows: Mapped[int] = mapped_column(Integer)
    flagged_rows: Mapped[int] = mapped_column(Integer)
    rule_counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    trust_score: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    threshold: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    outcome: Mapped[str] = mapped_column(Text)
    warned: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default=text("'{}'::text[]"))
    # 0034 (phase 7.14): rows with no flag and a waived exception (recounted by the database), and per rule
    waived_rows: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    waived_counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class ReportRow(Base):
    """Phase 7.15: one report of one data owner or data steward for one day or month, as a version. Append-only
    (migration 0035; trigger: the database sets numbers_sha256 from numbers and takes only the next version)."""

    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("kind", "subject_id", "subject_role", "period_start", "version"),
        CheckConstraint("kind IN ('daily', 'monthly')", name="kind"),
        CheckConstraint("subject_role IN ('data_owner', 'data_steward')", name="subject_role"),
        CheckConstraint("length(btrim(subject_id)) > 0 AND length(btrim(generated_by)) > 0", name="named"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("jsonb_typeof(numbers) = 'object'", name="numbers"),
        CheckConstraint(
            "(kind = 'daily' AND period_end = period_start + 1) "
            "OR (kind = 'monthly' AND period_start = date_trunc('month', period_start)::date "
            "AND period_end = (period_start + interval '1 month')::date)", name="period_fits_kind"),
        CheckConstraint("narrative_status IN ('written', 'refused', 'none')", name="narrative_status"),
        CheckConstraint(
            "(narrative_status = 'written' AND narrative IS NOT NULL AND length(btrim(narrative)) > 0 "
            "AND narrative_log_id IS NOT NULL) "
            "OR (narrative_status = 'refused' AND narrative IS NULL AND narrative_log_id IS NOT NULL "
            "AND narrative_note IS NOT NULL AND length(btrim(narrative_note)) > 0) "
            "OR (narrative_status = 'none' AND narrative IS NULL AND narrative_log_id IS NULL)",
            name="narrative_fits_status"),
        Index("ix_reports_subject", "subject_id", "kind", "period_start"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    kind: Mapped[str] = mapped_column(Text)
    subject_id: Mapped[str] = mapped_column(Text)
    subject_role: Mapped[str] = mapped_column(Text)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer)
    numbers: Mapped[dict[str, Any]] = mapped_column(JSONB)
    numbers_sha256: Mapped[str] = mapped_column(Text, default="")  # the database overwrites it (amaze_guard_report)
    narrative: Mapped[str | None] = mapped_column(Text)
    narrative_status: Mapped[str] = mapped_column(Text, default="none")
    narrative_note: Mapped[str | None] = mapped_column(Text)
    narrative_log_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proposal_log.id"))
    generated_by: Mapped[str] = mapped_column(Text)


class ExceptionResolutionRow(Base):
    """Phase 7.14: one approved exception fix of a held load's group (waived or referred), recorded. Append-only
    (migration 0034; trigger: an approved exception_fix of the feed, every step decided, matching this row)."""

    __tablename__ = "exception_resolutions"
    __table_args__ = (
        UniqueConstraint("proposal_id"),
        CheckConstraint("action IN ('waived', 'referred')", name="action"),
        CheckConstraint("applied_by IN ('agent', 'manual')", name="applied_by"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason"),
        Index("ix_exception_resolutions_feed_rule", "feed_id", "rule_key", "pattern"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    load_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("loads.id"))
    rule_key: Mapped[str] = mapped_column(Text)
    element_keys: Mapped[list[str]] = mapped_column(ARRAY(Text))
    pattern: Mapped[str] = mapped_column(Text)
    group_key: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"))
    decided_by: Mapped[str] = mapped_column(Text)
    applied_by: Mapped[str] = mapped_column(Text)


class FeedGoLiveRow(Base):
    """Step 11: the data owner's approval of a feed's configuration for loading, versioned per feed. Pins the
    mapping ids, rule ids and the test load (phase 6 plan, assumption 15). Append-only (migration 0018;
    trigger: an approved review_summary of the feed, decided by its registered owner, pinning exactly these)."""

    __tablename__ = "feed_go_lives"
    __table_args__ = (
        UniqueConstraint("feed_id", "version"),
        UniqueConstraint("proposal_id"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    version: Mapped[int] = mapped_column(Integer)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"))
    approved_by: Mapped[str] = mapped_column(Text)
    mapping_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    rule_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    # 0023: the feed's current transformations; the guard compares them with the feed's (task 4.2 writes them)
    transformation_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list,
                                                                server_default=text("'{}'::uuid[]"))
    test_load_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("loads.id"))
    hypercare_until: Mapped[date] = mapped_column(Date)
    # 0030 (phase 7.12, T3): the code version this approval pinned (the trigger requires it on every new go-live)
    code_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("code_versions.id"))


class CodeVersionRow(Base):
    """T3: the generated code of a feed (staging DDL, load and curated SQL, rule SQL, documentation) at one go-live
    approval. Append-only (migration 0030); `sha256` must be the hash of `files` (the trigger recomputes it)."""

    __tablename__ = "code_versions"
    __table_args__ = (UniqueConstraint("feed_id", "version"),)

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    version: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    files: Mapped[dict[str, str]] = mapped_column(JSONB)
    mapping_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    rule_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    transformation_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    test_load_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("loads.id"))
    test_load_result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    approved_by: Mapped[str] = mapped_column(Text)


class PipelineRunRow(Base):
    """Phase 7.17: one run of the code pipeline (commit, SQL parse, sandbox rule check, test load) over a feed's generated
    code, keyed by the code's SHA-256. Append-only (migration 0037); the trigger recomputes the outcome from the stages."""

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_feed_id_code_sha256", "feed_id", "code_sha256"),
        CheckConstraint("code_sha256 ~ '^[0-9a-f]{64}$'", name="code_sha256"),
        CheckConstraint("commit_hash IS NULL OR commit_hash ~ '^[0-9a-f]{40,64}$'", name="commit_hash"),
        CheckConstraint("length(btrim(run_by)) > 0", name="run_by"),
        CheckConstraint("outcome IN ('passed', 'failed_at_commit', 'failed_at_sql_parse', 'failed_at_rule_check', "
                        "'failed_at_test_load')", name="outcome"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    code_sha256: Mapped[str] = mapped_column(Text)
    commit_hash: Mapped[str | None] = mapped_column(Text)
    commit_created: Mapped[bool] = mapped_column(Boolean)
    stages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    outcome: Mapped[str] = mapped_column(Text)
    run_by: Mapped[str] = mapped_column(Text)


class LoadStageRow(Base):
    """Step 13: one stage (raw, curated, validated, standard) of one load. Append-only (migration 0018;
    trigger: written in order, while the load is received)."""

    __tablename__ = "load_stages"
    __table_args__ = (
        UniqueConstraint("load_id", "stage"),
        CheckConstraint("stage IN ('raw', 'curated', 'validated', 'standard')", name="stage"),
        CheckConstraint("outcome IN ('stage_completed', 'failed_rolled_back')", name="outcome"),
        CheckConstraint("rows_in >= 0 AND rows_out >= 0", name="counts"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    load_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("loads.id"))
    stage: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    rows_in: Mapped[int] = mapped_column(Integer)
    rows_out: Mapped[int] = mapped_column(Integer)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StandardRecordRow(Base):
    """Step 13: one file row in the standard model (element key -> value), with the keys of the rules that
    flagged it. Append-only (migration 0018; trigger: a received load of the same feed)."""

    __tablename__ = "standard_records"
    __table_args__ = (
        CheckConstraint("row_number >= 1", name="row_number"),
        Index("ix_standard_records_load_id", "load_id"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    load_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("loads.id"))
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    row_number: Mapped[int] = mapped_column(Integer)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default=text("'{}'::text[]"))
    # 0034 (phase 7.14): rule keys a current waiver turned from a flag into a waived exception (never also in flags)
    waived: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default=text("'{}'::text[]"))


class ProductRow(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("code"),
        CheckConstraint("code ~ '^[a-z][a-z0-9_]*$'", name="code_format"),
        CheckConstraint("domain ~ '^[a-z][a-z0-9_]*$'", name="domain_format"),
        CheckConstraint("length(btrim(name)) > 0", name="name_present"),
        CheckConstraint("length(btrim(owner_id)) > 0", name="owner_present"),
        CheckConstraint(_in("visibility", VISIBILITIES), name="visibility"),
        # 0019 (G15): one product per feed
        Index("uq_products_feed_id", "feed_id", unique=True, postgresql_where=text("feed_id IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requirements.id"))
    feed_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("feeds.id"))  # migration 0019: step 20
    owner_id: Mapped[str] = mapped_column(Text)  # migration 0006
    visibility: Mapped[str] = mapped_column(Text)  # migration 0006: open or restricted


class ProductElementRow(Base):
    """One glossary element a product holds. Append-only: the list is fixed when the product is created.

    A database trigger (migration 0006) refuses an element_key that has no row in glossary_elements.
    """

    __tablename__ = "product_elements"
    __table_args__ = (UniqueConstraint("product_id", "element_key"),)

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    element_key: Mapped[str] = mapped_column(Text)


class ProductGrantRow(Base):
    """One grant or revoke of access to a product, for a user or a role. Append-only.

    seq (an identity column the database always generates) gives the events a total order.
    """

    __tablename__ = "product_grants"
    __table_args__ = (
        UniqueConstraint("seq"),
        CheckConstraint("subject_type IN ('user', 'role')", name="subject_type"),
        CheckConstraint("action IN ('grant', 'revoke')", name="action"),
        CheckConstraint(f"subject_type <> 'role' OR {_in('subject', ROLES)}", name="known_role"),
        CheckConstraint("length(btrim(subject)) > 0 AND length(btrim(decided_by)) > 0", name="text_present"),
        Index("ix_product_grants_product_id", "product_id"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    subject_type: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(Text)


class ConsumerRequestRow(Base):
    """One "Request this data" and its routing outcome. Append-only."""

    __tablename__ = "consumer_requests"
    __table_args__ = (
        CheckConstraint(_in("outcome", ROUTING_OUTCOMES), name="outcome"),
        CheckConstraint(
            "(requirement_id IS NOT NULL) = (outcome = 'raise_requirement')", name="requirement_matches_outcome"
        ),
        CheckConstraint("cardinality(element_keys) + cardinality(terms) >= 1", name="something_requested"),
        CheckConstraint("length(btrim(requester_id)) > 0 AND length(btrim(query)) > 0", name="text_present"),
        Index("ix_consumer_requests_requester_id", "requester_id"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    requester_id: Mapped[str] = mapped_column(Text)
    query: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text, default="", server_default="")
    element_keys: Mapped[list[str]] = mapped_column(ARRAY(Text))
    terms: Mapped[list[str]] = mapped_column(ARRAY(Text))
    outcome: Mapped[str] = mapped_column(Text)
    product_codes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requirements.id"))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)


class UploadSourceRow(Base):
    """The source an uploaded file is (migration 0021; phase 7.8, task 2.4): one per feed, append-only, and the
    feed must read this code through the dropzone (trigger amaze_guard_upload_source)."""

    __tablename__ = "upload_sources"
    __table_args__ = (
        UniqueConstraint("code"),
        UniqueConstraint("feed_id"),
        CheckConstraint("code ~ '^upl_[a-z][a-z0-9_]*$'", name="code_format"),
        *(CheckConstraint(f"length(btrim({column})) > 0", name=column)
          for column in ("name", "provider", "description", "licence", "permitted_use", "redistribution",
                         "original_name", "original_ref", "manifest_ref", "created_by")),
        CheckConstraint("jsonb_typeof(manifest) = 'object'", name="manifest"),
        CheckConstraint(
            "COALESCE(jsonb_typeof(intake_options) = 'object' "
            "AND jsonb_typeof(intake_options -> 'sheet') IN ('string', 'null') "
            "AND jsonb_typeof(intake_options -> 'header_row') = 'number' "
            "AND (intake_options ->> 'header_row') ~ '^[1-9][0-9]*$', false)", name="intake_options"),
    )

    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = _created_at(clock=True)
    code: Mapped[str] = mapped_column(Text)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    name: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    licence: Mapped[str] = mapped_column(Text)
    permitted_use: Mapped[str] = mapped_column(Text)
    redistribution: Mapped[str] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(Text)
    original_ref: Mapped[str] = mapped_column(Text)
    manifest_ref: Mapped[str] = mapped_column(Text)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    intake_options: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(Text)


class AmazeSettingRow(Base):
    """One version of the system settings that the database guards read (migration 0020). Append-only; the
    latest version wins and no row means REQUIRE_FOUR_EYES is on."""

    __tablename__ = "amaze_settings"
    __table_args__ = (
        UniqueConstraint("version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("length(btrim(set_by)) > 0", name="set_by"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason"),
    )

    id: Mapped[uuid.UUID] = _id()
    version: Mapped[int] = mapped_column(Integer)
    require_four_eyes: Mapped[bool] = mapped_column(Boolean)
    set_by: Mapped[str] = mapped_column(Text)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str] = mapped_column(Text)


class OnboardingSessionRow(Base):
    """One feed's walk through the nine onboarding steps (migration 0024). Workflow state, not configuration: only
    current_step, step_states, status, updated_at and model_calls change; never deleted."""

    __tablename__ = "onboarding_sessions"
    __table_args__ = (
        UniqueConstraint("feed_id"),
        CheckConstraint(_in("current_step", ONBOARDING_STEPS), name="current_step"),
        CheckConstraint("amaze_valid_step_states(step_states)", name="step_states"),
        CheckConstraint(_in("status", SESSION_STATUSES), name="status"),
        CheckConstraint("length(btrim(created_by)) > 0", name="created_by"),
        CheckConstraint("model_calls >= 0", name="model_calls"),
        CheckConstraint(_in("run_mode", RUN_MODES), name="run_mode"),
    )

    id: Mapped[uuid.UUID] = _id()
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id"))
    current_step: Mapped[str] = mapped_column(Text)
    step_states: Mapped[dict[str, str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, default="active", server_default="active")
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at(clock=True)  # the moment of the insert: the list is oldest first
    updated_at: Mapped[datetime] = _created_at(clock=True)
    model_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 0032: step (Approve & next runs the next agent) or chain (every agent ahead runs on drafts); only step -> chain
    run_mode: Mapped[str] = mapped_column(Text, default="step", server_default="step")


class ProposalBaseRow(Base):
    """A draft and one upstream draft it was built on (migration 0032, phase 7.13). Append-only. Same feed, an earlier
    kind (glossary_element < mapping < transformation < rule), an agent's draft (trg_proposal_bases_a_guard); an
    approval of the draft is refused while a base is not approved (trg_approvals_c_bases_approved)."""

    __tablename__ = "proposal_bases"
    __table_args__ = (CheckConstraint("proposal_id <> base_id", name="not_itself"),)

    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"), primary_key=True)
    base_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"), primary_key=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # amaze_may_delete() reads it (0027): no FK, as proposals.feed_id
    created_at: Mapped[datetime] = _created_at()


class OnboardingEventRow(Base):
    """One thing that happened in an onboarding (migration 0024). Append-only; the audit timeline reads it."""

    __tablename__ = "onboarding_events"
    __table_args__ = (
        CheckConstraint(_in("step", ONBOARDING_STEPS), name="step"),
        CheckConstraint(_in("event", EVENT_NAMES), name="event"),
        CheckConstraint("length(btrim(actor)) > 0", name="actor"),
        CheckConstraint("jsonb_typeof(detail) = 'object'", name="detail_is_an_object"),
        Index("ix_onboarding_events_session_id_at", "session_id", "at"),
    )

    id: Mapped[uuid.UUID] = _id()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("onboarding_sessions.id"))
    step: Mapped[str] = mapped_column(Text)
    event: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = _created_at(clock=True)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proposals.id"))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class OnboardingJobRow(Base):
    """The queue of the onboarding worker (migration 0024), copied from glossary_runs: a lease, an attempt count, one
    queued or running job per session. A finished job never changes; never deleted."""

    __tablename__ = "onboarding_jobs"
    __table_args__ = (
        CheckConstraint(_in("step", ONBOARDING_STEPS), name="step"),
        CheckConstraint(_in("status", JOB_STATUSES), name="status"),
        CheckConstraint("attempt >= 0", name="attempt"),
        CheckConstraint("status <> 'running' OR lease_until IS NOT NULL", name="running_has_a_lease"),
        CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="failed_has_an_error"),
        CheckConstraint("(status IN ('done', 'failed', 'discarded')) = (finished_at IS NOT NULL)",
                        name="finished_at_matches_status"),
        Index("ix_onboarding_jobs_session_id", "session_id"),
        Index("uq_onboarding_jobs_one_active_per_session", "session_id", unique=True,
              postgresql_where=text("status IN ('queued', 'running')")),
    )

    id: Mapped[uuid.UUID] = _id()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("onboarding_sessions.id"))
    step: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="queued", server_default="queued")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at(clock=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
