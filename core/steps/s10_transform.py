"""Step 10, transformations and the key policy (phase 7.8 plan, assumptions 27 to 29).

Outcomes of the propose half: transforms_proposed, nothing_to_transform. Not outcomes: a stored transformation
(TransformationStored), a rejection (TransformationRejected), a stored key policy (KeyPolicySet).

The model-call pattern: transform_input() reads (the feed's mapped columns from its latest profile, less those that
already have a transformation or a pending proposal); the API ends that transaction; propose_transformations() runs
Agent.run(), whose before_write locks the feed row and checks again that the feed is registered, that each input
column is still mapped to the element the input held and has neither a transformation nor a pending proposal
(ConfigurationChangedMeanwhile, TransformationAlreadyProposed: a discard row, nothing written).

decide_transformation(): the data steward approves (the `transformations` row, version n+1, in the same transaction)
or rejects with a note. Lock order (CLAUDE.md): the feed row, then the proposal row; the next version is read after
the feed row is locked. set_key_policy(): the data steward sets what a load does with duplicated keys; until one is
set the policy is `flag` on the key columns of the latest approved profile summary (what the engine does).
The steps flush; the caller commits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.configuration import Mapping
from core.domain.profiling import FileColumn
from core.domain.transformation import ColumnToTransform, KeyPolicy, Transformation, TransformationInput
from core.engine.run import _key_policy as engine_key_policy  # the one place the default policy is decided
from core.repositories.configuration import MappingRepository
from core.repositories.errors import (
    ConfigurationChangedMeanwhile,
    FeedNotProfiled,
    FeedNotRegistered,
    NotAnApprover,
    NothingToTransform,
    ProposalAlreadyDecided,
    TransformationAlreadyProposed,
    WrongProposalKind,
)
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.transformations import KeyPolicyRepository, TransformationRepository
from core.steps.errors import StepInputError, needs_a_note
from core.steps.result import StepResult
from core.steps.s02_profile import actions_were_proposed, selected_actions
from core.steps.s1_scope import RunsAgent
from core.steps.samples import samples_for_model
from ports.config import Settings
from ports.identity.base import User


class UnknownKeyColumn(StepInputError):
    """A key column the steward named is not a column of the feed's latest profile."""


class TransformsProposed(StepResult):
    outcome: Literal["transforms_proposed"] = "transforms_proposed"
    log_id: UUID
    proposal_ids: list[UUID]


class NoTransformNeeded(StepResult):
    outcome: Literal["nothing_to_transform"] = "nothing_to_transform"
    log_id: UUID  # the call is logged even when it found nothing to clean


TransformResult = Annotated[TransformsProposed | NoTransformNeeded, Field(discriminator="outcome")]


class TransformationStored(BaseModel):
    """Not an outcome: the steward approved and the transformations row exists."""

    model_config = ConfigDict(frozen=True)

    transformation: Transformation


class TransformationRejected(BaseModel):
    """Not an outcome: the steward rejected the proposal; only the decision is written."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    source_field: str


class EffectiveKeyPolicy(BaseModel):
    """What a load does with duplicated keys now: the latest set policy, or the default (flag, on the key of the
    latest approved profile summary; no key columns means no duplicate check)."""

    model_config = ConfigDict(frozen=True)

    policy: Literal["hold", "keep_last", "flag"]
    key_columns: list[str]
    source: Literal["set", "default"]
    version: int | None = None
    set_by: str | None = None
    set_at: datetime | None = None
    reason: str | None = None


class KeyPolicySet(BaseModel):
    """Not an outcome: the next key policy version was stored."""

    model_config = ConfigDict(frozen=True)

    key_policy: KeyPolicy


# ---------------------------------------------------------------- the agent's input


def _mapped_columns(session: Session, feed_id: UUID, columns: list[FileColumn]) -> list[tuple[Mapping, FileColumn]]:
    """The feed's current mappings whose column is in the profile (matched in any case, as the engine does)."""
    by_lower = {c.name.lower(): c for c in columns}
    return [(m, by_lower[m.source_field.lower()]) for m in MappingRepository(session).current_for_feed(feed_id)
            if m.source_field.lower() in by_lower]


def transform_input(session: Session, *, feed_id: UUID, settings: Settings | None = None) -> TransformationInput:
    """Read only. FeedNotRegistered, FeedNotProfiled, NothingToTransform (no mapped column in the latest profile, or
    each has a transformation or a pending proposal). Samples only if SEND_SAMPLE_VALUES, and only of these columns."""
    feed = FeedRepository(session).get(feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}; only a registered feed is transformed")
    profile = FeedProfileRepository(session).latest(feed_id)
    if profile is None:
        raise FeedNotProfiled(f"Feed {feed.code!r} has no profile")
    mapped = _mapped_columns(session, feed_id, profile.columns)
    if not mapped:
        raise NothingToTransform(f"Feed {feed.code!r} has no mapped column to clean")
    done = {t.source_field for t in TransformationRepository(session).current_for_feed(feed_id)}
    proposals = ProposalRepository(session)
    open_columns = [(m, c) for m, c in mapped if m.source_field not in done
                    and proposals.pending_for_column("transformation", feed_id, m.source_field) is None]
    if not open_columns:
        raise NothingToTransform(f"Every mapped column of feed {feed.code!r} already has transformations or one "
                                 "waiting for the data steward")
    glossary = GlossaryRepository(session)
    samples = samples_for_model(profile.columns, settings or Settings(_env_file=None, send_sample_values=False))
    return TransformationInput(
        feed_id=feed.id, feed_code=feed.code,
        columns=[ColumnToTransform(
            source_field=m.source_field, element_key=m.element_key,
            element_type=glossary.get_current(m.element_key).data_type, duckdb_type=c.type, null_count=c.null_count,
            row_count=profile.row_count or 0, placeholder_values=list(c.placeholder_values),
            placeholder_count=c.placeholder_count, text_date_format=c.text_date_format,
            date_format_ambiguous=c.date_format_ambiguous, all_empty=c.all_empty, constant=c.constant)
            for m, c in open_columns],
        samples={m.source_field: samples[c.name] for m, c in open_columns if c.name in samples},
        profile_actions=selected_actions(session, feed.id),
        profile_actions_decided=actions_were_proposed(session, feed.id))


# ---------------------------------------------------------------- propose


def _still_as_input(session: Session, agent_input: TransformationInput) -> None:
    """before_write: the feed row locked, then each input column checked against what the input held."""
    feed = FeedRepository(session).lock(agent_input.feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} became {feed.stage} while the agent answered")
    current = {m.source_field: m.element_key for m in MappingRepository(session).current_for_feed(feed.id)}
    done = {t.source_field for t in TransformationRepository(session).current_for_feed(feed.id)}
    proposals = ProposalRepository(session)
    for column in agent_input.columns:
        if proposals.pending_for_column("transformation", feed.id, column.source_field) is not None:
            raise TransformationAlreadyProposed(f"Column {column.source_field!r} of feed {feed.code!r} got a pending "
                                                "transformation proposal while the agent answered")
        if current.get(column.source_field) != column.element_key:
            raise ConfigurationChangedMeanwhile(f"Column {column.source_field!r} of feed {feed.code!r} was mapped to "
                                                f"{current.get(column.source_field)} while the agent answered")
        if column.source_field in done:  # a proposal made and decided during the call leaves nothing pending
            raise ConfigurationChangedMeanwhile(f"Column {column.source_field!r} of feed {feed.code!r} got a "
                                                "transformation while the agent answered")


def propose_transformations(session: Session, *, agent: RunsAgent, agent_input: TransformationInput) -> TransformResult:
    run = agent.run(session, agent_input, before_write=lambda s: _still_as_input(s, agent_input))
    ids = [p.id for p in run.proposals]
    if not ids:
        return NoTransformNeeded(log_id=run.log_id)
    return TransformsProposed(log_id=run.log_id, proposal_ids=ids)


# ---------------------------------------------------------------- the steward's decision


def decide_transformation(session: Session, *, proposal_id: UUID, user: User, decision: Literal["approved", "rejected"],
                          note: str | None = None) -> TransformationStored | TransformationRejected:
    proposals = ProposalRepository(session)
    proposal = proposals.get(proposal_id)
    if proposal.kind != "transformation":
        raise WrongProposalKind(f"Proposal {proposal_id} is a {proposal.kind} proposal, not transformation")
    needs_a_note(decision, note)  # refused input, before any lock
    FeedRepository(session).lock(proposal.feed_id)  # before the proposal and the next version are read
    proposal = proposals.lock(proposal_id)
    if proposal.status != "pending":
        raise ProposalAlreadyDecided(f"Proposal {proposal_id} is already {proposal.status}")
    if "data_steward" not in user.roles:
        raise NotAnApprover(f"Proposal {proposal_id} needs role data_steward; {user.id} has {user.roles}")
    source_field = proposal.payload["source_field"]
    if decision == "rejected":
        proposals.record_decision(proposal_id, user=user, decision="rejected", note=note)
        return TransformationRejected(proposal_id=proposal_id, source_field=source_field)
    proposals.approve_my_steps(proposal_id, user=user, note=note)  # one step; four-eyes is about a second step
    row = TransformationRepository(session).add_version(
        feed_id=proposal.feed_id, source_field=source_field, steps=proposal.payload["steps"],
        proposal_id=proposal_id, created_by=user.id)
    return TransformationStored(transformation=row)


# ---------------------------------------------------------------- the key policy


def effective_key_policy(session: Session, feed_id: UUID) -> EffectiveKeyPolicy:
    """Read only. The latest set policy; none set: `flag` on the key columns of the latest approved profile summary."""
    chosen = KeyPolicyRepository(session).current(feed_id)
    if chosen is not None:
        return EffectiveKeyPolicy(policy=chosen.policy, key_columns=list(chosen.key_columns), source="set",
                                  version=chosen.version, set_by=chosen.set_by, set_at=chosen.set_at,
                                  reason=chosen.reason)
    default = engine_key_policy(session, feed_id)
    return EffectiveKeyPolicy(policy="flag", key_columns=list(default.key_columns) if default else [],
                              source="default")


def set_key_policy(session: Session, *, feed_id: UUID, user: User, policy: str, key_columns: list[str],
                   reason: str) -> KeyPolicySet:
    """The data steward sets the next key policy version. NotAnApprover, FeedNotRegistered, FeedNotProfiled,
    UnknownKeyColumn (a column the latest profile lacks)."""
    if "data_steward" not in user.roles:
        raise NotAnApprover(f"Setting the key policy needs role data_steward; {user.id} has {user.roles}")
    feed = FeedRepository(session).lock(feed_id)  # before the next version is read
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}; only a registered feed has a key policy")
    profile = FeedProfileRepository(session).latest(feed_id)
    if profile is None:
        raise FeedNotProfiled(f"Feed {feed.code!r} has no profile")
    known = {c.name for c in profile.columns}
    unknown = [c for c in key_columns if c not in known]
    if unknown:
        raise UnknownKeyColumn(f"Not columns of the latest profile of feed {feed.code!r}: {', '.join(unknown)}")
    return KeyPolicySet(key_policy=KeyPolicyRepository(session).set(feed_id, policy, key_columns, user.id, reason))
