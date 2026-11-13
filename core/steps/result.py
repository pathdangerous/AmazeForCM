"""StepResult: the shared base of every step's typed result (CLAUDE.md rule 3).

Each step declares one subclass per outcome, with `outcome` narrowed to a Literal of its CLAUDE.md
name, and returns their union, discriminated on `outcome`:

    class Defined(StepResult):
        outcome: Literal["defined"] = "defined"
        ...

    DefineRequirementResult = Annotated[Defined | ElementNotInGlossary, Field(discriminator="outcome")]

An expected negative case is one of these outcomes, never a bare error.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StepResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: str
