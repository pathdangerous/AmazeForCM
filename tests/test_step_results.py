"""Typed step results (rule 3; decision 9): each step returns a Pydantic discriminated union on
`outcome`, and the outcome names are CLAUDE.md's, letter for letter. Tasks 4 to 7 add a union each.
"""

import re
from typing import Literal

import pytest
from pydantic import ValidationError

from core.steps.result import StepResult
from ports.config import REPO_ROOT


def claude_md_outcomes(label: str) -> list[str]:
    """The names in backticks on CLAUDE.md's line "- **<label>**: ..." (steps and negative outcomes)."""
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.startswith(f"- **{label}**:")]
    assert len(lines) == 1, f"CLAUDE.md has {len(lines)} lines for {label!r}"
    return re.findall(r"`([a-z_]+)`", lines[0])


def test_claude_md_lists_the_phase_2_outcomes():
    assert claude_md_outcomes("17 Discover") == ["found", "not_entitled_visible"]
    assert claude_md_outcomes("Routing checks") == ["reuse", "compose", "create_product", "raise_requirement"]
    assert claude_md_outcomes("01 Define requirement") == ["defined", "element_not_in_glossary"]


class Done(StepResult):
    outcome: Literal["done"] = "done"
    detail: str


def test_a_step_result_is_frozen_and_forbids_extra_fields():
    result = Done(detail="x")
    with pytest.raises(ValidationError):
        result.detail = "y"
    with pytest.raises(ValidationError):
        Done(detail="x", surprise=1)
    with pytest.raises(ValidationError):
        Done(outcome="other", detail="x")
