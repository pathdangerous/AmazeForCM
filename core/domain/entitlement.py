"""Entitlement: who may use a restricted product.

Access is recorded as grant and revoke events (product_grants, append-only), for one user or for a
role. The decision itself is rules only and lives in core/entitlement/decide.py (CLAUDE.md rule 2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from core.domain.common import NonBlank
from ports.identity.base import ROLES

SubjectType = Literal["user", "role"]
GrantAction = Literal["grant", "revoke"]

AccessReason = Literal["open", "owner", "user_grant", "user_revoke", "role_grant", "no_grant"]
ACCESS_REASONS: tuple[str, ...] = get_args(AccessReason)


class GrantDraft(BaseModel):
    """One grant or revoke, validated before it is written."""

    model_config = ConfigDict(extra="forbid")

    subject_type: SubjectType
    subject: NonBlank  # a user id, or one of the seven roles
    action: GrantAction
    decided_by: NonBlank  # the user id of the person who granted or revoked

    @model_validator(mode="after")
    def _role_is_known(self) -> GrantDraft:
        if self.subject_type == "role" and self.subject not in ROLES:
            raise ValueError(f"{self.subject!r} is not a role")
        return self


class GrantEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    seq: int  # the order of events: the latest event is the one with the highest seq
    product_id: UUID
    subject_type: SubjectType
    subject: str
    action: GrantAction
    decided_by: str


class Access(BaseModel):
    """The entitlement decision for one user and one product, with the rule that decided it."""

    model_config = ConfigDict(frozen=True)

    entitled: bool
    reason: AccessReason
