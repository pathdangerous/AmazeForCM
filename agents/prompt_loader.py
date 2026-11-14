"""Versioned prompt files (phase 3 plan, assumption 24).

agents/prompts/<prompt_id>.<version>.yaml holds exactly four keys: prompt_id, version, system and user.
The user template ends with {input_json}: the agent's input, rendered as a fenced JSON block. That is
the message Azure OpenAI will get, and the block MockProvider's rules read (assumption 25).

A new version is a new file; an existing file is never edited (agents/registry.py holds their hashes).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from ports.llm.base import Message

PROMPTS_ROOT = Path(__file__).resolve().parent / "prompts"
INPUT_MARKER = "{input_json}"
_KEYS = {"prompt_id", "version", "system", "user"}


class InvalidPromptFile(ValueError):
    """A prompt file is malformed or does not match its file name."""


class UnknownPrompt(LookupError):
    """No current version is registered for this prompt id."""


def normalised_sha256(path: Path) -> str:
    """The file's hash with CRLF read as LF, so a checkout's line endings do not change it."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def render_input(agent_input: BaseModel) -> str:
    """The input as the fenced JSON block that ends every user message."""
    return "```json\n" + json.dumps(agent_input.model_dump(mode="json"), sort_keys=True) + "\n```"


class Prompt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_id: str
    version: str
    system: str
    user: str

    def render(self, agent_input: BaseModel) -> list[Message]:
        return [Message(role="system", content=self.system.strip()),
                Message(role="user", content=self.user.replace(INPUT_MARKER, render_input(agent_input)))]


def load_prompt(path: Path) -> Prompt:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != _KEYS:
        found = sorted(data) if isinstance(data, dict) else type(data).__name__
        raise InvalidPromptFile(f"{path.name}: needs exactly the keys {sorted(_KEYS)}, found {found}")
    if not all(isinstance(data[key], str) and data[key].strip() for key in _KEYS):
        raise InvalidPromptFile(f"{path.name}: every key must be non-empty text")
    expected = f"{data['prompt_id']}.{data['version']}.yaml"
    if path.name != expected:
        raise InvalidPromptFile(f"{path.name}: prompt_id and version say {expected}")
    user = data["user"]
    if user.count(INPUT_MARKER) != 1 or not user.rstrip().endswith(INPUT_MARKER):
        raise InvalidPromptFile(f"{path.name}: the user template must end with {INPUT_MARKER}, once")
    return Prompt(**data)


class PromptLibrary:
    """The current version of each prompt id. By default: agents/prompts and agents/registry."""

    def __init__(self, root: Path = PROMPTS_ROOT, versions: Mapping[str, str] | None = None):
        if versions is None:
            from agents.registry import PROMPT_VERSIONS

            versions = PROMPT_VERSIONS
        self._root, self._versions = Path(root), dict(versions)

    def get(self, prompt_id: str) -> Prompt:
        version = self._versions.get(prompt_id)
        if version is None:
            raise UnknownPrompt(f"No registered version of prompt {prompt_id!r}")
        return load_prompt(self._root / f"{prompt_id}.{version}.yaml")
