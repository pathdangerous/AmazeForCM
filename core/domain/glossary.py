"""GlossaryElement: one enterprise data element (for example isin, close_price), versioned.

The current element is the highest version of its key. Keys, domains and aliases are lower-case
snake_case. Identifier type names (isin, cusip, sedol, figi) are allowed; real identifier values are not
(rule 6: synthetic data only).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.domain.common import NonBlank as Text
from core.domain.common import SnakeName

DataType = Literal["string", "integer", "decimal", "date", "datetime", "boolean"]
DATA_TYPES: tuple[str, ...] = get_args(DataType)


class GlossaryElementDraft(BaseModel):
    """The content of one element version, validated before anything is written."""

    model_config = ConfigDict(extra="forbid")

    key: SnakeName
    name: Text
    domain: SnakeName
    data_type: DataType
    definition: Text
    aliases: list[SnakeName] = Field(default_factory=list)

    @model_validator(mode="after")
    def _aliases_distinct(self) -> GlossaryElementDraft:
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError("an alias can appear only once")
        if self.key in self.aliases:
            raise ValueError("an alias cannot repeat the key")
        return self


class GlossaryElement(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    key: str
    version: int
    name: str
    domain: str
    data_type: DataType
    definition: str
    aliases: list[str]
    created_by: str
    proposal_id: UUID | None  # None only for the seed
