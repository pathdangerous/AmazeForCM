"""Step 02 profile (phase 4 plan, assumptions 6 to 12): what DuckDB found in a feed's file.

FileColumn and FileProfile are the file's statistics, computed by code. Later tasks add the spec,
coverage against each requirement, and the stored profile.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
