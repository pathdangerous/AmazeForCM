"""What a person types to add an item at an onboarding stage (phase 7.11, T7). One model per proposal kind; each becomes
the same payload an agent's proposal of that kind has, so approval, the engine and the guards read it unchanged.

glossary_element  a new element or an alias, for one column
mapping           one column to any glossary element, or the column marked excluded (not loaded)
transformation    one column's steps, from the engine's allowed set (core/domain/transformation.py)
rule              a template the feed's elements are offered (by its rule key), or the rule's own key, SQL, description
                  and elements; it is back-tested before it is added
review_summary    a note on the go-live summary (an edit of the summary: its notes are the only thing that changes)
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.domain.common import NonBlank, SnakeName
from core.domain.mapping import ElementOption
from core.domain.transformation import TransformSteps


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualGlossary(_Item):
    kind: Literal["glossary_element"]
    source_field: NonBlank
    option: ElementOption
    reason: NonBlank


class ManualMapping(_Item):
    kind: Literal["mapping"]
    source_field: NonBlank
    element_key: SnakeName | None = None
    excluded: bool = False
    reason: NonBlank

    @model_validator(mode="after")
    def _one_or_the_other(self) -> ManualMapping:
        if self.excluded == (self.element_key is not None):
            raise ValueError("a column is mapped to an element, or excluded: one of the two")
        return self


class ManualTransformation(_Item):
    kind: Literal["transformation"]
    source_field: NonBlank
    steps: TransformSteps
    reason: NonBlank


class ManualRule(_Item):
    kind: Literal["rule"]
    template: SnakeName | None = None  # the rule key of an offer in the step's templates
    rule_key: SnakeName | None = None
    sql: NonBlank | None = None
    description: NonBlank | None = None
    element_keys: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _a_template_or_the_whole_rule(self) -> ManualRule:
        own = (self.rule_key, self.sql, self.description)
        if self.template is None and not all(own):
            raise ValueError("a rule is a template, or its own rule_key, sql and description")
        if self.template is not None and any(v is not None for v in own) or (self.template and self.element_keys):
            raise ValueError("a template rule takes everything from the template: give nothing else")
        return self


class ManualNote(_Item):
    kind: Literal["review_summary"]
    note: NonBlank


ManualItem = Annotated[ManualGlossary | ManualMapping | ManualTransformation | ManualRule | ManualNote,
                       Field(discriminator="kind")]
