"""MockProvider (LLM_PROVIDER=mock): deterministic outputs, from rules or fixtures.

- A prompt id with a rule (ports/llm/mock_rules.py, phase 3 plan assumption 25): the user message ends
  with the agent's input as a fenced JSON block; the rule computes the output from it. This is the same
  message Azure OpenAI will get.
- Any other prompt id: fixtures/llm/<prompt_id>/<prompt_version>.json, and the messages are ignored.
Either way the output is validated against response_schema. No randomness, no clock. A missing
fixture or input block is an error; nothing is invented.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel

from ports.config import REPO_ROOT
from ports.llm.base import LLMResult, Message
from ports.llm.mock_rules import RULES

DEFAULT_FIXTURES_ROOT = REPO_ROOT / "fixtures" / "llm"
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_INPUT_BLOCK = re.compile(r"```json\n(?P<body>.*)\n```\s*\Z", re.DOTALL)


class MockFixtureMissing(LookupError):
    """No fixture exists for this prompt id and version."""


class MockInputMissing(ValueError):
    """A prompt with a rule was called without its input block at the end of the user message."""


def _input_block(messages: list[Message]) -> dict:
    users = [m for m in messages if m.role == "user"]
    match = _INPUT_BLOCK.search(users[-1].content) if users else None
    if match is None:
        raise MockInputMissing("The last user message must end with a ```json block holding the agent's input")
    return json.loads(match.group("body"))


class MockProvider:
    name = "mock"

    def __init__(self, fixtures_root: Path = DEFAULT_FIXTURES_ROOT):
        self._root = Path(fixtures_root)

    def complete(
        self,
        *,
        prompt_id: str,
        prompt_version: str,
        messages: list[Message],
        response_schema: type[BaseModel],
        temperature: float = 0.0,
    ) -> LLMResult:
        for label, value in (("prompt_id", prompt_id), ("prompt_version", prompt_version)):
            if not _SAFE_NAME.match(value) or ".." in value:
                raise ValueError(f"{label} {value!r} must be lower-case letters, digits, '_', '-' or '.'")
        rule = RULES.get(prompt_id)
        if rule is not None:
            output = response_schema.model_validate(rule(_input_block(messages)))
        else:
            path = self._root / prompt_id / f"{prompt_version}.json"
            if not path.is_file():
                raise MockFixtureMissing(f"No mock fixture for {prompt_id} {prompt_version}: expected {path}")
            output = response_schema.model_validate(json.loads(path.read_text(encoding="utf-8")))
        return LLMResult(
            output=output,
            provider=self.name,
            model="mock",
            model_version="1",
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            latency_ms=0,
            raw_ref=None,
        )
