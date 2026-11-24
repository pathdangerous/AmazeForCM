"""Phase 7.17, T1: the Pipeline port and its local git adapter (plan 2.1, assumptions 3 to 5).

Every repository here is under tmp_path; the project's own repository is only ever read (git status), never written.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ports.config import REPO_ROOT, Settings
from ports.pipeline import get_pipeline
from ports.pipeline.base import PipelineRefused, PipelineUnavailable
from ports.pipeline.local import LocalGitPipeline

FILES = {"curated.sql": "-- curated\nSELECT 1;\n", "rules/r1.sql": "SELECT _row FROM raw;\n", "README.md": "# feed\n"}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def commit(pipeline, files=FILES, *, feed="feed_a", sha="a" * 64):
    return pipeline.commit(feed_code=feed, files=files, sha256=sha, by="dev-operator")


def project_status() -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"], capture_output=True, text=True,
                          check=True).stdout


def test_the_first_commit_makes_a_repository_of_its_own_with_one_commit(tmp_path):
    root = tmp_path / "repo"
    result = commit(LocalGitPipeline(root))
    assert result.created is True and len(result.commit) == 40
    assert (root / ".git").is_dir() and git(root, "rev-parse", "HEAD") == result.commit
    assert git(root, "rev-list", "--count", "HEAD") == "1"
    assert (root / "feed_a" / "rules" / "r1.sql").read_text(encoding="utf-8") == FILES["rules/r1.sql"]
    assert "feed_a: code aaaaaaaaaaaa" in git(root, "log", "-1", "--format=%B")


def test_the_same_files_again_add_no_commit_and_return_the_commit_that_holds_them(tmp_path):
    pipeline = LocalGitPipeline(tmp_path / "repo")
    first = commit(pipeline)
    again = commit(pipeline)
    assert (again.created, again.commit) == (False, first.commit)
    assert git(tmp_path / "repo", "rev-list", "--count", "HEAD") == "1"


def test_changed_files_are_a_new_commit_and_replace_only_this_feeds_folder(tmp_path):
    root = tmp_path / "repo"
    pipeline = LocalGitPipeline(root)
    commit(pipeline, feed="feed_b")
    first = commit(pipeline)
    second = commit(pipeline, {"curated.sql": "-- changed\n", "README.md": "# feed\n"}, sha="b" * 64)
    assert second.created is True and second.commit != first.commit
    assert not (root / "feed_a" / "rules" / "r1.sql").exists()  # the removed rule is gone from the folder
    assert (root / "feed_b" / "rules" / "r1.sql").exists()  # another feed's folder is untouched
    assert git(root, "rev-list", "--count", "HEAD") == "3"


def test_a_repository_inside_another_repository_commits_to_its_own(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "-C", str(outer), "init", "-q"], check=True)
    inner = outer / "inner"
    result = commit(LocalGitPipeline(inner))
    assert git(inner, "rev-parse", "HEAD") == result.commit
    assert subprocess.run(["git", "-C", str(outer), "rev-parse", "HEAD"], capture_output=True).returncode != 0  # no commit


@pytest.mark.parametrize("path", ["../escape.sql", "/abs.sql", "a\\b.sql", "C:/x.sql", "a//b.sql", ".git/config", ""])
def test_a_path_that_leaves_the_feed_folder_is_refused_and_nothing_is_written(tmp_path, path):
    root = tmp_path / "repo"
    with pytest.raises(PipelineRefused):
        commit(LocalGitPipeline(root), {path: "x", **FILES})
    assert not (tmp_path / "escape.sql").exists()
    assert not root.exists() or git(root, "rev-list", "--all", "--count") == "0"


@pytest.mark.parametrize("feed", ["", "../x", "a/b", ".hidden"])
def test_a_feed_code_that_is_not_one_folder_name_is_refused(tmp_path, feed):
    with pytest.raises(PipelineRefused):
        commit(LocalGitPipeline(tmp_path / "repo"), feed=feed)


@pytest.mark.parametrize("root", [REPO_ROOT, REPO_ROOT.parent])
def test_the_projects_own_repository_is_never_a_pipeline_root(root):
    with pytest.raises(PipelineRefused):
        commit(LocalGitPipeline(root))


def test_no_git_is_unavailable_not_a_crash(tmp_path):
    with pytest.raises(PipelineUnavailable):
        commit(LocalGitPipeline(tmp_path / "repo", git="no-such-git-executable"))


def test_a_commit_leaves_the_projects_git_status_as_it_was(tmp_path):
    before = project_status()
    commit(LocalGitPipeline(tmp_path / "repo"))
    assert project_status() == before


def test_the_factory_names_the_adapter_from_the_settings(tmp_path):
    local = get_pipeline(Settings(_env_file=None, pipeline_repo_root=tmp_path / "r"))
    assert (local.name, local.root) == ("local", (tmp_path / "r").resolve())
    azure = get_pipeline(Settings(_env_file=None, pipeline_provider="azure_devops"))
    assert azure.name == "azure_devops" and azure.is_stub is True


def test_the_default_root_is_under_the_git_ignored_data_folder(monkeypatch):
    monkeypatch.delenv("PIPELINE_REPO_ROOT")  # conftest points every test run at a temporary repository
    root = Settings(_env_file=None).pipeline_repo_root
    assert root == (REPO_ROOT / "data" / "pipeline-repo").resolve()
    ignored = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "-q", str(root / "feed_a" / "x.sql")])
    assert ignored.returncode == 0
