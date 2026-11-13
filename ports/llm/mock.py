"""MockProvider (LLM_PROVIDER=mock): deterministic, fixture-based outputs.

complete() loads fixtures/llm/<prompt_id>/<prompt_version>.json and validates it against
response_schema. Messages and temperature are ignored, so the same prompt id and version always
give the same result. No randomness, no clock. A missing fixture is an error; nothing is invented.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel

from ports.config import REPO_ROOT
from ports.llm.base import LLMResult, Message

DEFAULT_FIXTURES_ROOT = REPO_ROOT / "fixtures" / "llm"
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class MockFixtureMissing(LookupError):
    """No fixture exists for this prompt id and version."""


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
