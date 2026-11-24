"""The code pipeline (phase 7.17; plan docs/plans/phase-7.17.md, 2.2). No model call anywhere: deterministic code.

A feed's generated code (core/engine/codegen.py) is committed to the pipeline repository, its SQL is parsed, its rules pass the
sandbox's own check, and a dry test load runs the engine on the feed's latest fetched file. The run ends in a typed result:
passed, or failed_at_<the first stage that failed>. The data owner's go-live needs a passed run of the code it pins.

Like step 02, no connection is held while git, DuckDB or the file work:
    pipeline_plan()         read only: the feed is registered; the code, the configuration and the file to test-load
    ... the API ends the read transaction ...
    run_pipeline()          no database: the four stages, stopping at the first failure (the rest are skipped)
    record_pipeline_run()   locks the feed row, checks the configuration is still the one the code was made from, adds one row
The dry test load writes no load, no record and no notice.
"""

from __future__ import annotations

from uuid import UUID

import duckdb
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.domain.load import Load
from core.domain.pipeline_run import PIPELINE_STAGES, PipelineOutcome, PipelineRun, PipelineStage
from core.engine.run import Configuration, FailedRolledBack, LoadPlan, configuration_of, execute_load
from core.engine.sandbox import RuleRefused, check_rule_sql
from core.repositories.errors import ConfigurationChangedMeanwhile, FeedNotRegistered
from core.repositories.feeds import FeedRepository
from core.repositories.pipeline_runs import PipelineRunRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.codegen import code_for_feed
from ports.pipeline.base import Pipeline, PipelineRefused, PipelineUnavailable
from ports.storage.base import BlobStore


class PipelinePlan(BaseModel):
    """What one run needs, read in a transaction the caller then ends."""

    model_config = ConfigDict(frozen=True)

    feed_id: UUID
    feed_code: str
    files: dict[str, str]  # the generated code
    sha256: str  # its hash: the identity of the code version that will carry it
    configuration: Configuration
    blob_ref: str | None  # the feed's latest fetched file, the one the test load reads


class PipelineAttempt(BaseModel):
    """What run_pipeline() found, before it is written."""

    model_config = ConfigDict(frozen=True)

    feed_id: UUID
    code_sha256: str
    commit_hash: str | None
    commit_created: bool
    stages: list[PipelineStage]
    outcome: PipelineOutcome
    run_by: str


def pipeline_plan(session: Session, feed_id: UUID) -> PipelinePlan:
    """FeedNotFound, FeedNotRegistered."""
    feed = FeedRepository(session).get(feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}; only a registered feed has generated code to run")
    code = code_for_feed(session, feed_id)
    fetch = SourceFetchRepository(session).latest_fetched(feed_id)
    return PipelinePlan(feed_id=feed_id, feed_code=feed.code, files=code.files, sha256=code.sha256,
                        configuration=configuration_of(session, feed_id), blob_ref=fetch.blob_ref if fetch else None)


def run_pipeline(plan: PipelinePlan, *, pipeline: Pipeline, blobs: BlobStore, rule_timeout_seconds: float,
                 by: str) -> PipelineAttempt:
    """No database. The stages in order; after the first failure the rest are skipped."""
    stages: list[PipelineStage] = []
    commit = None
    for name in PIPELINE_STAGES:
        if stages and stages[-1].status != "passed":
            stages.append(PipelineStage(stage=name, status="skipped", log=["Skipped: an earlier stage failed."]))
            continue
        if name == "commit":
            commit, ok, log = _commit(plan, pipeline, by)
        elif name == "sql_parse":
            ok, log = _sql_parse(plan)
        elif name == "rule_check":
            ok, log = _rule_check(plan)
        else:
            ok, log = _test_load(plan, blobs, rule_timeout_seconds)
        stages.append(PipelineStage(stage=name, status="passed" if ok else "failed", log=log))
    failed = next((s.stage for s in stages if s.status == "failed"), None)
    return PipelineAttempt(feed_id=plan.feed_id, code_sha256=plan.sha256, commit_hash=commit.commit if commit else None,
                           commit_created=bool(commit and commit.created), stages=stages,
                           outcome=f"failed_at_{failed}" if failed else "passed", run_by=by)


def _commit(plan: PipelinePlan, pipeline: Pipeline, by: str):
    try:
        commit = pipeline.commit(feed_code=plan.feed_code, files=plan.files, sha256=plan.sha256, by=by)
    except (PipelineRefused, PipelineUnavailable) as exc:
        return None, False, [str(exc)]
    how = "a new commit" if commit.created else "already in the repository"
    return commit, True, [f"Committed {len(plan.files)} files as {commit.commit[:10]} ({how})."]


def _sql_parse(plan: PipelinePlan) -> tuple[bool, list[str]]:
    passed, failed = [], []
    for path in sorted(p for p in plan.files if p.endswith(".sql")):
        try:
            passed.append(f"{path}: {len(duckdb.extract_statements(plan.files[path]))} statements parse")
        except duckdb.Error as exc:
            failed.append(f"{path}: does not parse: {str(exc).splitlines()[0]}")
    return not failed, [*passed, *failed]


def _rule_check(plan: PipelinePlan) -> tuple[bool, list[str]]:
    passed, failed = [], []
    for path in sorted(p for p in plan.files if p.startswith("rules/") and p.endswith(".sql")):
        try:
            check_rule_sql(plan.files[path])
            passed.append(f"{path}: a plain SELECT the sandbox accepts")
        except RuleRefused as exc:
            failed.append(f"{path}: the sandbox refuses it: {exc}")
    return not failed, [*passed, *failed] or ["No rules."]


def _test_load(plan: PipelinePlan, blobs: BlobStore, rule_timeout_seconds: float) -> tuple[bool, list[str]]:
    if plan.blob_ref is None:
        return False, ["No fetched file to test-load: fetch or upload the feed's file first."]
    config = plan.configuration
    # the engine reads only the file's reference from the load; nothing here is written (shortcut: not a loads row)
    load_plan = LoadPlan(load=Load.model_construct(file_ref=plan.blob_ref), configuration_changed=False,
                         columns=config.columns, rules=config.rules, transformations=config.transformations,
                         key_policy=config.key_policy)
    outcome = execute_load(load_plan, blob_store=blobs, rule_timeout_seconds=rule_timeout_seconds)
    log = []
    for result in outcome.stages:
        if isinstance(result, FailedRolledBack):
            log.append(f"{result.stage}: failed ({result.reason}): {result.detail.get('message', '')}")
        else:
            log.append(f"{result.stage}: {result.rows_in} rows in, {result.rows_out} rows out")
    return outcome.failed is None, log


def record_pipeline_run(session: Session, attempt: PipelineAttempt) -> PipelineRun:
    """The feed row is locked first (lock order); then the code must still be the configuration's: ConfigurationChangedMeanwhile
    (409) and nothing written when a mapping, rule or transformation changed during the run."""
    feed = FeedRepository(session).lock(attempt.feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} became {feed.stage} while the pipeline ran")
    if code_for_feed(session, feed.id).sha256 != attempt.code_sha256:
        raise ConfigurationChangedMeanwhile(f"The configuration of feed {feed.code!r} changed while the pipeline ran; "
                                            "nothing was recorded. Run it again.")
    return PipelineRunRepository(session).add(
        feed_id=feed.id, code_sha256=attempt.code_sha256, commit_hash=attempt.commit_hash,
        commit_created=attempt.commit_created, stages=attempt.stages, outcome=attempt.outcome, run_by=attempt.run_by)
