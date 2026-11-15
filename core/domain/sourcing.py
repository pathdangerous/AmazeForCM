"""The custom data product (Known source?, S1-S4): which source a requirement was handed to, and
every fetch of a feed's source.

Phase 3a: SourceChoice (one per requirement, basis known_source) and SourceFetch (fetched or
source_down). Phase 3b adds the requester's pick, the scope and the agents' models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, model_validator

from core.domain.common import NonBlank
from core.domain.proposal import Proposal
from ports.connectors.catalogue import SourceConditions, SourceEntry


class AgentRun(BaseModel):
    """What agents/base.Agent.run() returns: the logged call, its validated output and the proposals
    it created (none for a read-only agent). Steps receive this; they never call a model themselves."""

    model_config = ConfigDict(frozen=True)

    log_id: UUID
    input_ref: str
    output: SerializeAsAny[BaseModel]
    proposals: list[Proposal]


class SourceSummary(BaseModel):
    """A catalogue source as shown to the requester: who provides it and its conditions of use."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    provider: str
    visibility: str
    conditions: SourceConditions

    @classmethod
    def of(cls, entry: SourceEntry) -> SourceSummary:
        return cls(code=entry.code, name=entry.name, provider=entry.provider, visibility=entry.visibility,
                   conditions=entry.conditions)

ChoiceBasis = Literal["known_source", "requester_pick"]
CHOICE_BASES: tuple[str, ...] = get_args(ChoiceBasis)
ChoiceAction = Literal["selected", "released"]
CHOICE_ACTIONS: tuple[str, ...] = get_args(ChoiceAction)
ReleaseReason = Literal["source_down_n_days", "requester_asked",
                        "candidate_rejected"]  # phase 4 (0014): the backend team rejected the candidate feed
RELEASE_REASONS: tuple[str, ...] = get_args(ReleaseReason)
Visibility = Literal["public", "private"]  # a scope's; not a product's visibility
SCOPE_VISIBILITIES: tuple[str, ...] = get_args(Visibility)

FetchOutcome = Literal["fetched", "source_down"]
FETCH_OUTCOMES: tuple[str, ...] = get_args(FetchOutcome)


class SourceChoice(BaseModel):
    """One version of a requirement's source choice (0009): a selection (a known source, or the
    requester's pick with the conditions accepted) or a release. The highest version is current."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    requirement_id: UUID
    version: int
    action: ChoiceAction
    feed_id: UUID
    source_code: str
    basis: ChoiceBasis | None
    reason: ReleaseReason | None
    proposal_id: UUID | None
    accepted_conditions: SourceConditions | None
    decided_by: str | None


# ---------------------------------------------------------------- agents' inputs and outputs (assumption 21)


class ElementRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    name: str


class ChannelRef(BaseModel):
    """A channel of the catalogue (where sources are found) and the visibility of its sources."""

    model_config = ConfigDict(frozen=True)

    channel: str
    visibility: Visibility


class IntentInput(BaseModel):
    """S1: what the IntentAgent sees."""

    model_config = ConfigDict(frozen=True)

    requirement_id: UUID
    requirement_text: str
    purpose: str | None
    note: str | None
    elements: list[ElementRef]
    terms: list[str]
    channels: list[ChannelRef]


class IntentOutput(BaseModel):
    """S1: the proposed scope. Also the scope proposal's payload."""

    visibility: Visibility
    provider: NonBlank
    where_to_search: list[NonBlank]
    reason: NonBlank


class Coverage(BaseModel):
    """What a source covers of a requirement, computed by code from the catalogue (assumption 30)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    elements: list[str]  # requested elements the source declares
    terms: list[str]  # ticked terms equal to one of the source's other fields
    missing: list[str]  # requested elements and terms it does not cover


class CandidateRef(BaseModel):
    """S2: one catalogue source in scope, as the SourceDiscoveryAgent sees it. Its conditions and
    coverage come from the catalogue and are copied into the proposal; the model only ranks."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    provider: str
    description: str
    elements: list[str]
    other_fields: list[str]
    conditions: SourceConditions
    coverage: Coverage


class SourceDiscoveryInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    requirement_id: UUID
    requested_elements: list[str]
    requested_terms: list[str]
    scope_visibility: Visibility
    where_to_search: list[str]
    candidates: list[CandidateRef]


class RankedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no coverage or conditions from the model

    source_code: str
    reason: NonBlank


class SourceDiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ranked: list[RankedSource]


class GlossaryRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    aliases: list[str]


class AssistantInput(BaseModel):
    """Step 17: what the DiscoveryAssistant sees."""

    model_config = ConfigDict(frozen=True)

    query: str
    unmatched_terms: list[str]
    glossary: list[GlossaryRef]


class SuggestedElement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    element_key: str
    reason: NonBlank


class AssistantOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[SuggestedElement]


class RequirementScopeDraft(BaseModel):
    """S1: what the requester confirmed, exactly as the IntentAgent proposed it."""

    model_config = ConfigDict(extra="forbid")

    visibility: Visibility
    provider: NonBlank
    where_to_search: list[NonBlank] = Field(min_length=1)


class RequirementScope(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    requirement_id: UUID
    version: int
    visibility: Visibility
    provider: str
    where_to_search: list[str]
    proposal_id: UUID
    confirmed_by: str


class SourceFetchDraft(BaseModel):
    """One fetch, validated before it is written: a fetched file has a blob and a size above zero; a
    source_down has a reason and no file."""

    model_config = ConfigDict(extra="forbid")

    outcome: FetchOutcome
    blob_ref: NonBlank | None = None
    size_bytes: int | None = Field(default=None, gt=0)
    detail: NonBlank | None = None

    @model_validator(mode="after")
    def _consistent(self) -> SourceFetchDraft:
        if self.outcome == "fetched" and (self.blob_ref is None or self.size_bytes is None or self.detail is not None):
            raise ValueError("a fetched row has a blob_ref and a size_bytes, and no detail")
        if self.outcome == "source_down" and (self.detail is None or self.blob_ref is not None
                                              or self.size_bytes is not None):
            raise ValueError("a source_down row has a detail, and no blob_ref or size_bytes")
        return self


class SourceFetch(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    outcome: FetchOutcome
    blob_ref: str | None
    size_bytes: int | None
    detail: str | None
