"""Pipeline port (phase 7.17): where a feed's generated code is committed, one commit per code version."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class PipelineRefused(ValueError):
    """The feed code, a file path or the repository root is not allowed. Nothing was written."""


class PipelineUnavailable(RuntimeError):
    """The code store cannot be reached (no git, or git failed). The pipeline run reports it as failed_at_commit."""


@dataclass(frozen=True)
class CommitResult:
    commit: str  # the commit that holds these files
    created: bool  # False: the folder already held exactly these files, so no commit was added


def check_relative_path(path: str) -> str:
    """One relative path with '/' separators, no empty, '.', '..' or dot-named segment. Shared by every adapter."""
    parts = path.split("/")
    if not path or "\\" in path or ":" in path or any(p == "" or p.startswith(".") for p in parts):
        raise PipelineRefused(f"Not an allowed path: {path!r}")
    return path


class Pipeline(Protocol):
    name: str

    def commit(self, *, feed_code: str, files: dict[str, str], sha256: str, by: str) -> CommitResult:
        """Make `files` the whole content of the feed's folder and commit it, once per distinct content.
        Raises PipelineRefused or PipelineUnavailable."""
        ...
