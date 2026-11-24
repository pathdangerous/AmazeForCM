"""Phase 7.17, T2: POST/GET /api/amaze/feeds/{id}/pipeline-runs and the go-live that needs a passed run (plan 2.2).
The code goes to a temporary pipeline repository (tests/conftest.py), never to ./data/pipeline-repo."""

from __future__ import annotations

from uuid import UUID

import pytest

from core.repositories.feeds import FeedRepository
from tests.test_api_amaze_go_live import DEVON, call, decide, exchange, kicks, map_the_columns  # noqa: F401 (fixtures)
from tests.test_api_consumer_sourcing import RILEY, client  # noqa: F401 (client is a fixture)

pytestmark = pytest.mark.anyio

STAGES = ["commit", "sql_parse", "rule_check", "test_load"]


async def ready(client, exchange):
    exchange.job.run()  # the file is in the store
    await map_the_columns(client, exchange.id)


async def test_a_run_goes_through_the_four_stages_and_the_list_is_newest_first(client, exchange):
    await ready(client, exchange)
    first = await call(client, "POST", f"feeds/{exchange.id}/pipeline-runs", {})
    assert first["outcome"] == "passed" and [s["stage"] for s in first["stages"]] == STAGES
    assert all(s["status"] == "passed" and s["log"] for s in first["stages"])
    assert len(first["commit_hash"]) == 40 and first["commit_created"] is True
    assert first["run_by"] == "dev-backend-team" and first["code_version"] is None  # no go-live has approved this code yet
    second = await call(client, "POST", f"feeds/{exchange.id}/pipeline-runs", {})
    assert (second["commit_hash"], second["commit_created"]) == (first["commit_hash"], False)  # the same code
    listed = await call(client, "GET", f"feeds/{exchange.id}/pipeline-runs")
    assert [r["id"] for r in listed] == [second["id"], first["id"]]


async def test_the_go_live_is_refused_with_409_until_a_run_passed_then_the_runs_name_the_version(client, exchange):
    await ready(client, exchange)
    summary = (await call(client, "POST", f"feeds/{exchange.id}/go-live/summary", {}))["result"]["proposal_id"]
    refused = await decide(client, summary, DEVON, expect=409)
    assert "Run the pipeline" in refused["detail"]
    await call(client, "POST", f"feeds/{exchange.id}/pipeline-runs", {})
    approved = await decide(client, summary, DEVON)
    assert approved["result"]["outcome"] == "approved_scheduled"
    [run] = await call(client, "GET", f"feeds/{exchange.id}/pipeline-runs")
    assert run["code_version"] == 1  # the run is now of code version 1


async def test_a_run_that_fails_is_a_200_with_its_typed_outcome_and_the_commit_kept(client, exchange):
    # no file was fetched into the store, so the test load cannot read one
    run = await call(client, "POST", f"feeds/{exchange.id}/pipeline-runs", {})
    assert run["outcome"] == "failed_at_test_load"
    assert [s["status"] for s in run["stages"]] == ["passed", "passed", "passed", "failed"]
    assert run["commit_hash"]  # the code that was tried is in the repository


async def test_a_candidate_feed_has_no_pipeline_and_an_unknown_feed_is_404(client, db_session):
    feed = FeedRepository(db_session).create_candidate(code="syn_pipeline_api", name="Candidate")
    await call(client, "POST", f"feeds/{feed.id}/pipeline-runs", {}, expect=409)
    await call(client, "POST", f"feeds/{UUID(int=9)}/pipeline-runs", {}, expect=404)
    await call(client, "GET", f"feeds/{UUID(int=9)}/pipeline-runs", expect=404)


async def test_only_the_backend_team_runs_the_pipeline(client, exchange):
    await call(client, "POST", f"feeds/{exchange.id}/pipeline-runs", {}, headers=RILEY, expect=403)
