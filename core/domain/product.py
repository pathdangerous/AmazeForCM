"""Product: a data product end users search for and reuse (steps 17 to 22).

A product has an owner, a visibility (open: everyone may use it; restricted: the owner and those
granted access) and at least one glossary element. Its element list is fixed when it is created.
Creation from a released load arrives with phase 7.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.domain.common import NonBlank, SnakeName

Visibility = Literal["open", "restricted"]
VISIBILITIES: tuple[str, ...] = get_args(Visibility)


class ProductDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: SnakeName
    name: NonBlank
    domain: SnakeName
    description: str = ""
    requirement_id: UUID | None = None
    owner_id: NonBlank
    visibility: Visibility
    element_keys: list[SnakeName] = Field(min_length=1)

    @model_validator(mode="after")
    def _elements_distinct(self) -> ProductDraft:
        if len(set(self.element_keys)) != len(self.element_keys):
            raise ValueError("an element can appear only once")
        return self


class Product(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    code: str
    name: str
    domain: str
    description: str
    requirement_id: UUID | None
    owner_id: str
    visibility: Visibility


class ProductWithElements(Product):
    """A product and the keys of the glossary elements it holds, in alphabetical order."""

    element_keys: list[str]
