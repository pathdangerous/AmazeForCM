"""Step 02 profile (phase 4 plan, assumptions 6 to 12): what DuckDB found in a feed's file.

FileColumn and FileProfile are the file's statistics, computed by code. Later tasks add the spec,
coverage against each requirement, and the stored profile.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.domain.common import NonBlank


class FileColumn(BaseModel):
    """One column of the file, in file order. null_rate is None for a file with no data rows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str  # DuckDB's type, detected from every row
    null_count: int
    null_rate: float | None
    distinct_count: int  # exact; nulls are not counted


class FileProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    row_count: int
    columns: list[FileColumn]
    engine: str  # "duckdb <version>"


class SourceSpec(BaseModel):
    """What the catalogue entry declared when the profile was made (kept with the profile as evidence)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_code: str
    elements: list[str]
    other_fields: list[str]
    columns: dict[str, str]  # file column -> declared field


class ItemCoverage(BaseModel):
    """One requested element or ticked term against the spec and the file. in_file is None when no file
    was read; the statistics are None when the column is not in the file (or the file has no rows)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item: str
    kind: Literal["element", "term"]
    declared: bool
    column: str | None
    in_file: bool | None
    null_rate: float | None
    distinct_count: int | None


class RequirementCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requirement_id: UUID
    items: list[ItemCoverage]  # elements, then terms, each in the requirement's order
    missing: list[str]  # not declared, or declared and absent from the file


# ---------------------------------------------------------------- stored rows (migration 0014)

ProfileOutcome = Literal["profiled", "spec_only_low_confidence"]
PROFILE_OUTCOMES: tuple[str, ...] = get_args(ProfileOutcome)
SignoffOutcome = Literal["meets", "meets_with_gaps", "rejected"]
SIGNOFF_OUTCOMES: tuple[str, ...] = get_args(SignoffOutcome)


class FeedProfileDraft(BaseModel):
    """One profile, validated before it is written: profiled has data rows and no detail; spec only has
    no data rows and says why."""

    model_config = ConfigDict(extra="forbid")

    feed_id: UUID
    fetch_id: UUID
    outcome: ProfileOutcome
    row_count: int | None = Field(default=None, ge=0)
    columns: list[FileColumn]
    coverage: list[RequirementCoverage]
    spec: SourceSpec
    detail: NonBlank | None = None
    engine: NonBlank
    profiled_by: NonBlank

    @model_validator(mode="after")
    def _consistent(self) -> FeedProfileDraft:
        if self.outcome == "profiled" and not (self.row_count and self.columns and self.detail is None):
            raise ValueError("a profiled row has data rows, columns and no detail")
        if self.outcome == "spec_only_low_confidence" and (self.row_count or self.detail is None):
            raise ValueError("a spec-only profile has no data rows and says why")
        return self


class FeedProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    version: int
    fetch_id: UUID
    outcome: ProfileOutcome
    row_count: int | None
    columns: list[FileColumn]
    coverage: list[RequirementCoverage]
    spec: SourceSpec
    detail: str | None
    engine: str
    profiled_by: str


class FeedSignoffDraft(BaseModel):
    """meets: no gap, no reason; meets_with_gaps: at least one gap; rejected: at least one reason."""

    model_config = ConfigDict(extra="forbid")

    feed_id: UUID
    profile_id: UUID
    proposal_id: UUID
    outcome: SignoffOutcome
    gaps: list[NonBlank] = Field(default_factory=list)
    reasons: list[NonBlank] = Field(default_factory=list)
    signed_off_by: NonBlank

    @model_validator(mode="after")
    def _fits(self) -> FeedSignoffDraft:
        fits = {"meets": not self.gaps and not self.reasons,
                "meets_with_gaps": bool(self.gaps) and not self.reasons,
                "rejected": bool(self.reasons) and not self.gaps}[self.outcome]
        if not fits:
            raise ValueError("meets has no gap or reason; meets_with_gaps has gaps only; rejected has reasons only")
        return self


class FeedSignoff(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    version: int
    profile_id: UUID
    proposal_id: UUID
    outcome: SignoffOutcome
    gaps: list[str]
    reasons: list[str]
    signed_off_by: str
