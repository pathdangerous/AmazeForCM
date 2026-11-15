"""Step 02 profile (phase 4 plan, assumptions 6 to 12): what DuckDB found in a feed's file.

FileColumn and FileProfile are the file's statistics, computed by code. Later tasks add the spec,
coverage against each requirement, and the stored profile.
"""

from __future__ import annotations

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
