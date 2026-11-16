"""Steps 05 and 06 (phase 5 plan): candidate elements, new columns and the glossary builder's options.

A Candidate is one glossary element offered for one column or term (core/steps/candidates.py builds the
lists; no agent is ever sent the whole glossary). A NewColumn is a column of a registered feed's latest
profile that step 05 looks at. An ElementOption is one thing the glossary owner may approve.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from core.domain.common import SnakeName
from core.domain.glossary import DataType, GlossaryElementDraft


class Candidate(BaseModel):
    """One glossary element offered for a column or a term, with how well its name matched."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name: str
    data_type: DataType
    definition: str
    aliases: list[str]
    score: float
    from_hint: bool
    type_fits: bool


class NewColumn(BaseModel):
    """A column step 05 looks at. type is DuckDB's, None when unknown; declared_as is the spec's other-field
    name when the source declares the column as one; sent_back when step 06 found no element for it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str | None
    null_rate: float | None = None
    distinct_count: int | None = None
    declared_as: str | None = None
    sent_back: bool = False


class ElementOption(BaseModel):
    """new_element: version 1 of element.key; alias: the next version of key with alias added."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["new_element", "alias"]
    key: SnakeName
    alias: SnakeName | None = None
    element: GlossaryElementDraft | None = None

    @model_validator(mode="after")
    def _shape(self) -> ElementOption:
        if self.kind == "new_element" and not (self.element is not None and self.alias is None
                                               and self.element.key == self.key):
            raise ValueError("a new element option holds the element, under its own key, and no alias")
        if self.kind == "alias" and not (self.alias is not None and self.element is None):
            raise ValueError("an alias option holds the alias and no element")
        return self
