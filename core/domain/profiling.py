"""Step 02 profile (phase 4 plan, assumptions 6 to 12): what DuckDB found in a feed's file.

FileColumn and FileProfile are the file's statistics, computed by code. Later tasks add the spec,
coverage against each requirement, and the stored profile.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from core.domain.common import NonBlank


class FileColumn(BaseModel):
    """One column of the file, in file order. null_rate is None for a file with no data rows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str  # DuckDB's type, detected from every row
    null_count: int
    null_rate: float | None
    distinct_count: int  # exact; nulls are not counted
    # Phase 7.8, task 3.1 (assumptions 23 and 24): facts found by code. Defaults keep earlier stored profiles loading.
    placeholder_count: int = 0  # non-null cells that are ALL, NA, N/A, - or blank; a null is not one
    placeholder_values: list[str] = []  # the distinct spellings seen, as they appear: at most 5, sorted
    text_date_format: str | None = None  # every value of a VARCHAR column fits this date format
    date_format_ambiguous: bool = False  # day-first was chosen although month-first fitted too
    all_empty: bool = False  # every row is null or a placeholder
    constant: bool = False  # one distinct real value, in at least two rows
    samples: list[str] = []  # at most 5 distinct real values, sorted, each cut to 64 characters


IssueKind = Literal["duplicate_key", "placeholder_only", "placeholders", "date_as_text", "all_empty", "constant"]


class ProfileIssue(BaseModel):
    """Something code found wrong or odd in the file. Never written by a model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: IssueKind
    columns: list[str]
    count: int
    detail: str


def _spellings(values: list[str]) -> str:
    return ", ".join(repr(v) for v in values)


def issues_of(row_count: int | None, columns: list[FileColumn]) -> list[ProfileIssue]:
    """The issues the column facts show, in column order, one per column and kind. Pure: the same facts give
    the same issues, so a stored profile needs no column of its own for them. (duplicate_key is separate.)"""
    rows = row_count or 0
    found: list[ProfileIssue] = []
    for c in columns:
        def issue(kind: IssueKind, count: int, detail: str, name: str = c.name) -> None:
            found.append(ProfileIssue(kind=kind, columns=[name], count=count, detail=detail))

        if c.all_empty and c.placeholder_count == 0:
            issue("all_empty", rows, "Every row is empty.")
            continue
        if c.all_empty:
            issue("placeholder_only", c.placeholder_count,
                  f"Every value is empty or a placeholder: {_spellings(c.placeholder_values)}.")
            continue
        if c.placeholder_count:
            issue("placeholders", c.placeholder_count,
                  f"{c.placeholder_count} of {rows} rows hold a placeholder: {_spellings(c.placeholder_values)}.")
        if c.text_date_format:
            note = " (ambiguous: day first assumed, month first fits too)" if c.date_format_ambiguous else ""
            issue("date_as_text", rows - c.null_count,
                  f"Dates held as text, format {c.text_date_format}{note}.")
        if c.constant:
            value = c.samples[0] if c.samples else ""
            issue("constant", rows, f"Every row holds the same value: {value!r}.")
    return found


class FileProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    row_count: int
    columns: list[FileColumn]
    engine: str  # "duckdb <version>"
    issues: list[ProfileIssue] = []  # computed by code from the columns (issues_of)


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


# ---------------------------------------------------------------- the ProfilerAgent (assumptions 14 and 15)


# ---------------------------------------------------------------- T1 (phase 7.12): facts in plain words, and actions

ActionKind = Literal["parse_date", "placeholder_to_null", "key_policy", "exclude_column", "add_rule",
                     # T4 (drift on a new delivery), proposed by code at step 12 or by a person
                     "map_new_column", "review_missing_column", "accept_type_change", "review_rule"]
ACTION_KINDS: tuple[str, ...] = get_args(ActionKind)
AppliedBy = Literal["agent", "manual"]  # "agent applies" (a later agent does it from the approved action) or "I'll do it"


class IncompleteRecords(BaseModel):
    """Rows with a null in any required column (the key columns and the columns of the requested items). Counted by code."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    incomplete: int
    total: int
    required_columns: list[str]


class GlossaryFit(BaseModel):
    """Each column against the glossary, by code: matched (a name or alias fits), new (nothing is close: the glossary
    builder will propose an element) or unmatched (something is close, but a person must choose)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matched: list[str] = []
    new: list[str] = []
    unmatched: list[str] = []


class ProposedAction(BaseModel):
    """What the ProfilerAgent proposes to do about something found in the file. A proposal (rule 1): a person selects it
    (approves) or rejects it. `applied_by` says who carries it out once selected."""

    model_config = ConfigDict(extra="forbid")

    action: ActionKind
    columns: list[str] = Field(default_factory=list)
    params: dict[str, str] = Field(default_factory=dict)
    applied_by: AppliedBy
    reason: NonBlank


class ProfilerInput(BaseModel):
    """What the ProfilerAgent sees: one stored profile, and the title of each requirement it covers."""

    model_config = ConfigDict(frozen=True)

    feed_id: UUID
    feed_code: str
    profile_id: UUID
    outcome: ProfileOutcome
    row_count: int | None
    columns: list[FileColumn]
    detail: str | None
    spec: SourceSpec
    coverage: list[RequirementCoverage]
    requirement_titles: dict[UUID, str]
    # Phase 7.8, task 3.2: what code found in the file, and (only when SEND_SAMPLE_VALUES is true, decision D3)
    # at most 5 values per column. The columns above carry no samples.
    issues: list[ProfileIssue] = []
    samples: dict[str, list[str]] = {}
    glossary_fit: GlossaryFit | None = None  # T1: found by code from the glossary
    # Where the file is stored: used by the step to count duplicates, never shown to the model.
    file_ref: str | None = Field(default=None, exclude=True)


class DuplicateKey(BaseModel):
    """Counted by code over the file for the key columns the model proposed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    columns: list[str]
    extra_rows: int
    examples: list[str]  # up to five duplicated key values


class ProfilerOutput(BaseModel):
    """The summary, the recommended outcome and the proposed key. No statistics: every number shown comes from
    the profile. duplicate_key and unknown_key_columns are filled by code (after_answer) and left empty by the model."""

    model_config = ConfigDict(extra="forbid")

    summary: NonBlank
    recommended_outcome: SignoffOutcome
    gaps: list[NonBlank]
    reasons: list[NonBlank]
    key_columns: list[str]  # the smallest set that should identify a row; may be empty
    key_reason: NonBlank
    confidence: float = Field(ge=0, le=1)
    duplicate_key: DuplicateKey | None = None
    unknown_key_columns: list[str] = []
    # T1: the two facts below are filled by code (after_answer); the model only proposes `actions`
    incomplete_records: IncompleteRecords | None = None
    glossary_fit: GlossaryFit | None = None
    actions: list[ProposedAction] = []


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def issues(self) -> list[ProfileIssue]:
        """Found again from the stored columns (no migration): the same facts give the same issues."""
        return issues_of(self.row_count, self.columns)


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
