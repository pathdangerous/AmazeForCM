"""The TableExtractorAgent's input and output (phase 7.8, task 2.3, decision D4).

A scanned PDF has only image pages. The pages go to the model as images, one page at a time in page order; the model
answers with the table it reads. The operator confirms (or rejects) it as an extracted_table proposal before a feed
is made from it. The images travel inside the input as base64 PNG strings in `page_images`; the prompt loader sends
them as image parts and keeps them out of the JSON block of the message, which holds the page numbers and the
SHA-256 of each PNG instead.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from core.domain.common import NonBlank


class TableExtractionInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    upload_id: UUID
    filename: str
    pages: list[int]  # 1-based page numbers, in the order of page_images
    page_images: list[str]  # base64 PNG, one per page
    page_hashes: list[str]  # SHA-256 (hex) of each PNG

    @model_validator(mode="after")
    def _one_image_and_one_hash_per_page(self) -> TableExtractionInput:
        if not len(self.pages) == len(self.page_images) == len(self.page_hashes):
            raise ValueError("pages, page_images and page_hashes must have one entry per page")
        return self


class ExtractedTable(BaseModel):
    """What the model read: a header, the rows (an empty cell is null) and how sure it is. No header and no
    rows, with confidence 0, is the honest answer for a page it cannot read."""

    model_config = ConfigDict(extra="forbid")

    header: list[NonBlank]
    rows: list[list[str | None]]
    confidence: float  # 0 to 1
    notes: list[str]  # anything unreadable or doubtful, one per entry

    @field_validator("confidence")
    @classmethod
    def _a_fraction(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def _every_row_has_one_cell_per_column(self) -> ExtractedTable:
        width = len(self.header)
        for number, row in enumerate(self.rows, start=1):
            if len(row) != width:
                raise ValueError(f"row {number} has {len(row)} cells; the header has {width}")
        return self
