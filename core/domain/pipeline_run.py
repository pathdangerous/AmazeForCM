"""One run of the code pipeline over a feed's generated code (phase 7.17; migration 0037). Append-only evidence: a run never
changes a mapping, rule or version. The result is typed: passed, or failed at the first stage that failed."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PipelineStageName = Literal["commit", "sql_parse", "rule_check", "test_load"]
PIPELINE_STAGES: tuple[str, ...] = get_args(PipelineStageName)  # in the order the pipeline runs them

StageStatus = Literal["passed", "failed", "skipped"]

PipelineOutcome = Literal["passed", "failed_at_commit", "failed_at_sql_parse", "failed_at_rule_check", "failed_at_test_load"]
PIPELINE_OUTCOMES: tuple[str, ...] = get_args(PipelineOutcome)


class PipelineStage(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: PipelineStageName
    status: StageStatus
    log: list[str] = Field(default_factory=list)


class PipelineRun(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    feed_id: UUID
    code_sha256: str  # the hash of the generated files: the identity of a code version, stored at the go-live approval
    commit_hash: str | None  # None only when the commit stage failed
    commit_created: bool  # False: the repository already held exactly this code
    stages: list[PipelineStage]
    outcome: PipelineOutcome
    run_by: str
