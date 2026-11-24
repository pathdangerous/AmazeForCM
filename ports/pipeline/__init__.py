"""Pipeline port. get_pipeline() returns the adapter named by PIPELINE_PROVIDER (local | azure_devops)."""

from __future__ import annotations

from ports.config import Settings, get_settings
from ports.pipeline.base import CommitResult, Pipeline, PipelineRefused, PipelineUnavailable

__all__ = ["CommitResult", "Pipeline", "PipelineRefused", "PipelineUnavailable", "get_pipeline"]


def get_pipeline(settings: Settings | None = None) -> Pipeline:
    settings = settings or get_settings()
    if settings.pipeline_provider == "azure_devops":
        from ports.pipeline.azure_devops import AzureDevOpsPipeline

        return AzureDevOpsPipeline()
    from ports.pipeline.local import LocalGitPipeline

    return LocalGitPipeline(settings.pipeline_repo_root)
