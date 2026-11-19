"""UploadSource: the source an uploaded file is (phase 7.8, task 2.4; plan assumption 17).

An operator uploads a file with no consumer request. Amaze keeps the original, writes the canonical CSV as the
feed's fetched file and records here how the file was read and under what conditions the operator accepted it.
Every step sees the upload as a SourceEntry of the combined catalogue (as_entry()); it is never listed to a
requester (SourceCatalogue.with_uploads).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from core.domain.common import NonBlank
from ports.connectors.catalogue import SourceConditions, SourceEntry

UploadCode = Annotated[str, StringConstraints(pattern=r"^upl_[a-z][a-z0-9_]*$")]
CODE_PREFIX = "upl_"


class IntakeOptions(BaseModel):
    """How the operator confirmed the file be read: the sheet (None for a CSV or PDF) and the header row (1-based)."""

    model_config = ConfigDict(extra="forbid")

    sheet: str | None
    header_row: int = Field(ge=1)


class UploadSourceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: UploadCode
    feed_id: UUID
    name: NonBlank
    provider: NonBlank
    description: NonBlank
    licence: NonBlank
    permitted_use: NonBlank
    redistribution: NonBlank
    original_name: NonBlank
    original_ref: NonBlank  # the BlobStore key of the uploaded original
    manifest_ref: NonBlank  # the BlobStore key of the intake manifest
    manifest: dict[str, Any]
    intake_options: IntakeOptions
    created_by: NonBlank


class UploadSource(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    code: str
    feed_id: UUID
    name: str
    provider: str
    description: str
    licence: str
    permitted_use: str
    redistribution: str
    original_name: str
    original_ref: str
    manifest_ref: str
    manifest: dict[str, Any]
    intake_options: dict[str, Any]
    created_by: str

    def as_entry(self) -> SourceEntry:
        """The catalogue entry every step reads: known, private, channel uploads, no declared fields (the file's own
        columns are its profile), the conditions the operator typed. source_ref is the feed's own."""
        return SourceEntry(
            code=self.code, name=self.name, provider=self.provider, description=self.description,
            visibility="private", channel="uploads", known=True,
            conditions=SourceConditions(licence=self.licence, permitted_use=self.permitted_use,
                                        redistribution=self.redistribution),
            elements=[], other_fields=[], connector="dropzone",
            source_ref=f"dropzone/{self.code.removeprefix(CODE_PREFIX)}", columns={})
