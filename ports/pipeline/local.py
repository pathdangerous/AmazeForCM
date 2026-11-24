"""Local Pipeline (PIPELINE_PROVIDER=local): a git repository of its own, one folder per feed, one commit per code version.

The repository is `root` (default ./data/pipeline-repo, git-ignored) with its own `.git`, never this project's: the adapter
refuses the project root or any folder that holds it. git runs by subprocess with an identity passed on the command line,
so Windows and Replit behave alike and no global git configuration is needed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ports.config import REPO_ROOT
from ports.pipeline.base import CommitResult, PipelineRefused, PipelineUnavailable, check_relative_path

IDENTITY = ("-c", "user.name=Amaze pipeline", "-c", "user.email=pipeline@amaze.local", "-c", "commit.gpgsign=false",
            "-c", "core.autocrlf=false")


class LocalGitPipeline:
    name = "local"

    def __init__(self, root: Path, git: str = "git"):
        self.root = Path(root).resolve()
        self._git = git

    def commit(self, *, feed_code: str, files: dict[str, str], sha256: str, by: str) -> CommitResult:
        if "/" in feed_code:
            raise PipelineRefused(f"A feed code is one folder name: {feed_code!r}")
        check_relative_path(feed_code)
        if not files:
            raise PipelineRefused("No files to commit")
        for path in files:
            check_relative_path(path)
        if self.root == REPO_ROOT or self.root in REPO_ROOT.parents:
            raise PipelineRefused(f"{self.root} holds this project: the pipeline keeps its code in a repository of its own")
        self._ensure_repository()
        folder = self.root / feed_code
        if folder.exists():
            shutil.rmtree(folder)
        for path, text in files.items():
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))  # bytes, so Windows does not change the line endings
        self._run("add", "-A", "--", feed_code)
        if not self._run("status", "--porcelain", "--", feed_code):
            return CommitResult(commit=self._run("log", "-1", "--format=%H", "--", feed_code), created=False)
        self._run("commit", "-q", "--no-verify", "-m", f"{feed_code}: code {sha256[:12]}",
                  "-m", f"code-sha256: {sha256}\nrun-by: {by}")
        return CommitResult(commit=self._run("rev-parse", "HEAD"), created=True)

    def _ensure_repository(self) -> None:
        if not (self.root / ".git").exists():
            self.root.mkdir(parents=True, exist_ok=True)
            self._run("init", "-q")
        if Path(self._run("rev-parse", "--show-toplevel")).resolve() != self.root:
            raise PipelineRefused(f"{self.root} is not the top of its own repository")

    def _run(self, *args: str) -> str:
        env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        try:
            done = subprocess.run([self._git, "-C", str(self.root), *IDENTITY, *args], capture_output=True, text=True,
                                  encoding="utf-8", env=env, check=False)
        except (FileNotFoundError, OSError) as exc:
            raise PipelineUnavailable(f"git could not be started: {exc}") from None
        if done.returncode != 0:
            problem = (done.stderr.strip() or done.stdout.strip()).splitlines()
            raise PipelineUnavailable(f"git {args[0]} failed: {problem[0] if problem else done.returncode}")
        return done.stdout.strip()
