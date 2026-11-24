"""Azure DevOps Pipeline. STUB: implement after the move to the office laptop.

Plan for the implementation: one Azure Repos repository AZURE_DEVOPS_REPO in project AZURE_DEVOPS_PROJECT of
AZURE_DEVOPS_ORG_URL, a folder per feed, one commit per code version through the Git REST API (a push with one commit);
the token comes from the Secrets port. Same rules as the local adapter: check paths with
ports.pipeline.base.check_relative_path, and no new commit for content the folder already holds.
"""

from __future__ import annotations

from pathlib import Path

from ports.config import AZURE_NOT_IMPLEMENTED, DEFAULT_ENV_FILE, read_env
from ports.pipeline.base import CommitResult


class AzureDevOpsPipeline:
    name = "azure_devops"
    is_stub = True  # /api/health reports this adapter as unavailable
    ENV_KEYS = ("AZURE_DEVOPS_ORG_URL", "AZURE_DEVOPS_PROJECT", "AZURE_DEVOPS_REPO")

    def __init__(self, env_file: Path | None = DEFAULT_ENV_FILE):
        self.config = read_env(self.ENV_KEYS, env_file=env_file)

    def commit(self, *, feed_code: str, files: dict[str, str], sha256: str, by: str) -> CommitResult:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)
