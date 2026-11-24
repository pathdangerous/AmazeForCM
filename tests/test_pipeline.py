"""Phase 7.17, T2: the pipeline step (plan docs/plans/phase-7.17.md, 2.2) on a feed onboarded as far as its go-live.

Each run commits to a repository under tmp_path; the project's own git status is checked to be the same afterwards."""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text

from core.repositories.errors import ConfigurationChangedMeanwhile, FeedNotRegistered, PipelineNotPassed
from core.repositories.feeds import FeedRepository
from core.repositories.pipeline_runs import PipelineRunRepository
from core.steps.codegen import code_for_feed
from core.steps.onboarding import approve_and_next
from core.steps.pipeline import pipeline_plan, record_pipeline_run, run_pipeline
from ports.config import REPO_ROOT
from ports.pipeline.local import LocalGitPipeline
from tests.step_chains import end_read, run_the_pipeline
from tests.test_onboarding import OPERATOR, make_world, run_to


@pytest.fixture
def w(db_session, glossary, tmp_path):
    world = make_world(db_session, tmp_path)
    run_to(world, "go_live")
    return world


def repo_of(tmp_path) -> LocalGitPipeline:
    return LocalGitPipeline(tmp_path / "pipeline-repo")


def head(tmp_path) -> str:
    return subprocess.run(["git", "-C", str(tmp_path / "pipeline-repo"), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def attempt_with(w, tmp_path, pipeline=None, **changes):
    """The plan of the feed, changed as given, run but not recorded."""
    plan = pipeline_plan(w.session, w.feed_id).model_copy(update=changes)
    end_read(w.session)
    return plan, run_pipeline(plan, pipeline=pipeline or repo_of(tmp_path), blobs=w.ctx.blob_store, rule_timeout_seconds=5,
                                by="dev-operator")


def statuses(run) -> list[str]:
    return [s.status for s in run.stages]


def test_a_registered_feed_passes_and_the_run_records_the_commit_and_the_codes_hash(w, tmp_path):
    run = run_the_pipeline(w.session, w.feed_id, pipeline=repo_of(tmp_path), blobs=w.ctx.blob_store)
    assert run.outcome == "passed" and statuses(run) == ["passed"] * 4
    assert [s.stage for s in run.stages] == ["commit", "sql_parse", "rule_check", "test_load"]
    assert run.commit_hash == head(tmp_path) and run.commit_created is True
    assert run.code_sha256 == code_for_feed(w.session, w.feed_id).sha256 and run.run_by == "dev-operator"
    assert any("rows" in line for line in run.stages[3].log)  # the test load says how many rows it read


def test_the_same_code_again_is_a_second_run_on_the_same_commit(w, tmp_path):
    first = run_the_pipeline(w.session, w.feed_id, pipeline=repo_of(tmp_path), blobs=w.ctx.blob_store)
    second = run_the_pipeline(w.session, w.feed_id, pipeline=repo_of(tmp_path), blobs=w.ctx.blob_store)
    assert (second.commit_hash, second.commit_created) == (first.commit_hash, False)
    assert len(PipelineRunRepository(w.session).list_for_feed(w.feed_id)) == 2


def test_the_pipeline_leaves_the_projects_git_status_as_it_was(w, tmp_path):
    def status() -> str:
        return subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"], capture_output=True, text=True,
                              check=True).stdout

    before = status()
    run_the_pipeline(w.session, w.feed_id, pipeline=repo_of(tmp_path), blobs=w.ctx.blob_store)
    assert status() == before


def test_a_pipeline_that_cannot_commit_fails_at_commit_and_skips_the_rest(w, tmp_path):
    plan, attempt = attempt_with(w, tmp_path, LocalGitPipeline(tmp_path / "r", git="no-such-git-executable"))
    run = record_pipeline_run(w.session, attempt)
    assert (run.outcome, statuses(run), run.commit_hash) == ("failed_at_commit", ["failed", "skipped", "skipped", "skipped"], None)
    assert "git" in run.stages[0].log[0]


def test_sql_that_does_not_parse_fails_at_sql_parse_with_the_commit_kept(w, tmp_path):
    plan = pipeline_plan(w.session, w.feed_id)
    end_read(w.session)
    plan, attempt = attempt_with(w, tmp_path, files={**plan.files, "curated.sql": "SELEC broken FROM;"})
    run = record_pipeline_run(w.session, attempt)
    assert (run.outcome, statuses(run)) == ("failed_at_sql_parse", ["passed", "failed", "skipped", "skipped"])
    assert run.commit_hash == head(tmp_path)  # the code that was tried is in the repository
    assert "curated.sql" in run.stages[1].log[-1]


@pytest.mark.parametrize("sql", ["DELETE FROM raw", "SELECT 1; SELECT 2"])
def test_a_rule_the_sandbox_would_refuse_fails_at_rule_check(w, tmp_path, sql):
    plan = pipeline_plan(w.session, w.feed_id)
    end_read(w.session)
    _, attempt = attempt_with(w, tmp_path, files={**plan.files, "rules/bad_rule.sql": sql})
    run = record_pipeline_run(w.session, attempt)
    assert (run.outcome, statuses(run)) == ("failed_at_rule_check", ["passed", "passed", "failed", "skipped"])
    assert "bad_rule" in run.stages[2].log[-1]


def test_a_feed_with_no_fetched_file_fails_at_test_load(w, tmp_path):
    _, attempt = attempt_with(w, tmp_path, blob_ref=None)
    run = record_pipeline_run(w.session, attempt)
    assert (run.outcome, statuses(run)) == ("failed_at_test_load", ["passed", "passed", "passed", "failed"])
    assert "no fetched file" in run.stages[3].log[-1].lower()


def test_a_file_the_engine_cannot_load_fails_at_test_load_naming_the_stage(w, tmp_path):
    _, attempt = attempt_with(w, tmp_path, blob_ref="raw/does-not-exist.csv")
    run = record_pipeline_run(w.session, attempt)
    assert run.outcome == "failed_at_test_load" and "raw" in run.stages[3].log[-1]


def test_the_dry_test_load_writes_no_load_and_no_record(w, tmp_path):
    def counts():
        return tuple(w.session.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in ("loads", "standard_records", "load_stages"))

    before = counts()
    run_the_pipeline(w.session, w.feed_id, pipeline=repo_of(tmp_path), blobs=w.ctx.blob_store)
    assert counts() == before


def test_a_configuration_that_changed_during_the_run_is_not_recorded(w, tmp_path):
    _, attempt = attempt_with(w, tmp_path, sha256="f" * 64)
    with pytest.raises(ConfigurationChangedMeanwhile):
        record_pipeline_run(w.session, attempt)
    assert PipelineRunRepository(w.session).list_for_feed(w.feed_id) == []


def test_a_feed_that_is_not_registered_has_no_pipeline(db_session, glossary):
    feed = FeedRepository(db_session).create_candidate(code="syn_pipeline_candidate", name="Candidate")
    with pytest.raises(FeedNotRegistered):
        pipeline_plan(db_session, feed.id)


# ---------------------------------------------------------------- the go-live needs a passed run


def go_live(w):
    return approve_and_next(w.session, session_id=w.session_id, user=OPERATOR, ctx=w.ctx)


def test_the_go_live_is_refused_with_no_run_and_nothing_is_written(w):
    with pytest.raises(PipelineNotPassed, match="Run the pipeline"):
        go_live(w)
    assert w.session.execute(text("SELECT count(*) FROM feed_go_lives")).scalar_one() == 0
    assert w.session.execute(text("SELECT count(*) FROM code_versions")).scalar_one() == 0


@pytest.mark.parametrize("stage", ["commit", "sql_parse", "rule_check", "test_load"])
def test_a_run_that_failed_at_any_stage_blocks_the_go_live(w, tmp_path, stage):
    plan = pipeline_plan(w.session, w.feed_id)
    end_read(w.session)
    changes = {
        "commit": {},
        "sql_parse": {"files": {**plan.files, "curated.sql": "SELEC broken FROM;"}},
        "rule_check": {"files": {**plan.files, "rules/bad_rule.sql": "DELETE FROM raw"}},
        "test_load": {"blob_ref": None},
    }[stage]
    pipeline = LocalGitPipeline(tmp_path / "r", git="no-such-git-executable") if stage == "commit" else repo_of(tmp_path)
    _, attempt = attempt_with(w, tmp_path, pipeline, **changes)
    assert attempt.outcome == f"failed_at_{stage}"
    record_pipeline_run(w.session, attempt)
    with pytest.raises(PipelineNotPassed):
        go_live(w)


def test_a_passed_run_lets_the_go_live_through_and_the_version_it_pins_has_the_runs_hash(w, tmp_path):
    run = run_the_pipeline(w.session, w.feed_id, pipeline=repo_of(tmp_path), blobs=w.ctx.blob_store)
    go_live(w)
    sha = w.session.execute(text("SELECT sha256 FROM code_versions WHERE feed_id = :f"), {"f": w.feed_id}).scalar_one()
    assert sha == run.code_sha256


def test_a_pass_counts_for_the_code_it_ran_and_for_no_other(w, tmp_path):
    run = run_the_pipeline(w.session, w.feed_id, pipeline=repo_of(tmp_path), blobs=w.ctx.blob_store)
    runs = PipelineRunRepository(w.session)
    assert runs.has_passed(w.feed_id, run.code_sha256) and not runs.has_passed(w.feed_id, "0" * 64)
