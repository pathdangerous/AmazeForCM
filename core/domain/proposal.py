"""Proposal and Approval (CLAUDE.md rule 1: AI proposes, people decide).

A proposal is what an agent suggests from one logged model call (proposal_log). Each named person's
decision on it is one Approval. Only an approved proposal can become versioned configuration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ports.identity.base import Role

ProposalKind = Literal[
    "scope",             # S1 IntentAgent
    "source_list",       # S2 SourceDiscoveryAgent
    "profile_summary",   # 02 ProfilerAgent
    "glossary_element",  # 05 GlossaryBuilderAgent
    "mapping",           # 06 MappingAgent
    "rule",              # 09 RuleRecommenderAgent
    "review_summary",    # 11 ReviewSummariserAgent
    "extracted_table",   # 00 TableExtractorAgent (phase 7.8): no feed yet; the feed is made when it is confirmed
    "transformation",    # 10 TransformationAgent (phase 7.8): safe steps for one mapped column of a feed
]
PROPOSAL_KINDS: tuple[str, ...] = get_args(ProposalKind)

Autonomy = Literal["A1", "A2"]  # A3 is not used in the PoC
ProposalStatus = Literal["pending", "approved", "rejected", "superseded"]  # superseded: edited (phase 7.8, 0024)
EDITABLE_KINDS: tuple[str, ...] = ("glossary_element", "mapping", "transformation", "rule")  # what an edit may change
Decision = Literal["approved", "rejected"]


class ProposalDraft(BaseModel):
    """What is needed to create a proposal. Validated before anything is written."""

    model_config = ConfigDict(extra="forbid")

    kind: ProposalKind
    payload: dict[str, Any]
    autonomy: Autonomy
    required_approver_roles: list[Role] = Field(min_length=1)  # decided in this order
    requirement_id: UUID | None = None
    feed_id: UUID | None = None

    @field_validator("required_approver_roles")
    @classmethod
    def _each_role_once(cls, roles: list[str]) -> list[str]:
        if len(set(roles)) != len(roles):
            raise ValueError("a role can appear only once: one person cannot decide two steps")
        return roles


class Proposal(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    proposal_log_id: UUID
    kind: ProposalKind
    payload: dict[str, Any]
    autonomy: Autonomy
    required_approver_roles: list[Role]
    status: ProposalStatus
    decided_at: datetime | None
    requirement_id: UUID | None
    feed_id: UUID | None
    edited_from: UUID | None = None  # the proposal this one replaces (0024); the old one is superseded
    edited_by: str | None = None  # who edited it


class Approval(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime  # when the decision was made
    proposal_id: UUID
    step: int = Field(ge=1)
    role: Role
    decided_by: str = Field(min_length=1)
    decision: Decision
    note: str | None
