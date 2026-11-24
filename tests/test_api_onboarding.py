"""The onboarding API, its worker and the resume after a restart (phase 7.8, task 5.4; plan assumptions 31 to 39).

- The whole path through the API on the mock (four-eyes off, the operator holds every role): upload, confirm (the
  session starts in the same transaction), the profile job, then Approve & next with the worker (`drain()`) between
  the steps, one edit visible in the timeline, one `decision_needed` 409, one Back, a 403 for a non-backend user.
- One commit-visibility test per committing endpoint (happy path), on the app's real session factory and a schema of
  its own, read back on a separate connection.
- The worker is started at start-up and takes the jobs the last process left (queued, or running with an expired
  lease). The glossary run is never run twice by the two workers. No connection is held during a model call.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import text

from agents.glossary_builder import GlossaryBuilderAgent
from agents.mapping import MappingAgent
from agents.profiler import ProfilerAgent
from agents.review_summariser import ReviewSummariserAgent
from agents.rule_recommender import RuleRecommenderAgent
from agents.transformation import TransformationAgent
from apps.api.jobs import DailyFetch, GlossaryRuns, OnboardingJobs, kick_onboarding_jobs
from core.repositories.onboarding import OnboardingJobRepository, OnboardingRepository
from core.repositories.products import ProductRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.settings import FourEyesRepository
from core.seed import seed_glossary
from core.steps.onboarding import OnboardingContext, StepAgents
from ports.config import Settings
from ports.connectors.dropzone import DropzoneConnector
from ports.db import get_sessionmaker
from ports.identity.dev import DevIdentity
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.conftest import running_app
from tests.test_commit_visibility import committed, own_schema  # noqa: F401 (own_schema is a fixture)

pytestmark = pytest.mark.anyio

DEMO = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic" / "Synthetic_Security_Master.xlsx"
IDENTITY = DevIdentity()
OP = {"X-Dev-User": "dev-operator"}
REQUESTER = {"X-Dev-User": "dev-requester"}
STEWARD = {"X-Dev-User": "dev-data-steward"}
OCTET = {"Content-Type": "application/octet-stream"}
BASE = "/api/amaze/onboardings"
CONFIRM = {"filename": DEMO.name, "feed_code": "syn_security_master", "feed_name": "Security master", "sheet": None,
           "header_row": None}


class Doubting:
    """The MockProvider, except that the transformation agent doubts the first column it cleans (confidence 0.5); the
    plain mock for every other agent, rules included (task 5.3b). The same provider as tests/test_onboarding.py."""

    name = "doubting"

    def __init__(self) -> None:
        self.inner = MockProvider()

    def complete(self, **kw):
        result = self.inner.complete(**kw)
        if kw["prompt_id"] != "transformation":
            return result
        data, doubted = result.output.model_dump(), False
        for answer in data["columns"]:
            if answer["steps"] and not doubted:
                answer["confidence"], doubted = 0.5, True
        return result.model_copy(update={"output": kw["response_schema"].model_validate(data)})


class During:
    """Runs `hook` where the model would be thinking, then answers as the mock does."""

    name = "during"

    def __init__(self, hook) -> None:
        self.inner, self.hook, self.calls = MockProvider(), hook, 0

    def complete(self, **kw):
        self.calls += 1
        self.hook()
        return self.inner.complete(**kw)


class Counting:
    name = "counting"

    def __init__(self) -> None:
        self.inner, self.calls = MockProvider(), 0

    def complete(self, **kw):
        self.calls += 1
        return self.inner.complete(**kw)


def agents_for(provider, notes) -> StepAgents:
    return StepAgents(
        profiler=ProfilerAgent(provider=provider, blob_store=notes),
        glossary_builder=GlossaryBuilderAgent(provider=provider, blob_store=notes),
        mapper=MappingAgent(provider=provider, blob_store=notes),
        transformer=TransformationAgent(provider=provider, blob_store=notes),
        rule_recommender=RuleRecommenderAgent(provider=provider, blob_store=notes),
        summariser=ReviewSummariserAgent(provider=provider, blob_store=notes))


def make_context(settings, blobs, dropzone, provider, notes, sf) -> OnboardingContext:
    def connectors(name: str):
        assert name == "dropzone", name
        return DropzoneConnector(dropzone)

    daily = DailyFetch(
        session_factory=sf, blob_store=blobs, connector_factory=connectors, settings=settings,
        backend_team=lambda: ["dev-operator"], operations=lambda: ["dev-operator"],
        roles_of=lambda user_id: IDENTITY.get_user(user_id).roles,
        display_name=lambda user_id: IDENTITY.get_user(user_id).display_name,
        trust_release_threshold=settings.trust_release_threshold)
    return OnboardingContext(agents=agents_for(provider, notes), blob_store=blobs, settings=settings, identity=IDENTITY,
                             load_now=daily.fetch_and_load, connector_factory=connectors)


# ---------------------------------------------------------------- the API on the test's one rolled-back session


@pytest.fixture
async def world(db_session, migrated_engine, tmp_path):
    from apps.api.deps import get_blob_store_port, get_llm_provider_port
    from apps.api.main import app

    FourEyesRepository(db_session).set(False, set_by="test", reason="Synthetic.")
    seed_glossary(db_session)
    db_session.commit()
    blobs, dropzone = LocalBlobStore(tmp_path / "blob"), tmp_path / "dropzone"
    # The default release threshold: the rules that flag valid rows are rejected by the operator (settle, task 5.3b).
    settings = Settings(_env_file=None, dropzone_root=dropzone)
    provider = Doubting()
    sf = lambda: nullcontext(db_session)  # noqa: E731 (the one session, as tests/test_onboarding.py does)
    ctx = make_context(settings, blobs, dropzone, provider, LocalBlobStore(tmp_path / "agent-blob"), sf)
    worker = OnboardingJobs(session_factory=sf, context_factory=lambda: ctx)
    async with running_app(engine=migrated_engine, settings=settings, session=db_session) as client:
        app.dependency_overrides[get_blob_store_port] = lambda: blobs
        app.dependency_overrides[get_llm_provider_port] = lambda: provider
        yield SimpleNamespace(client=client, worker=worker, ctx=ctx, session=db_session, app=app, blobs=blobs,
                              settings=settings, sf=sf, dropzone=dropzone)


async def upload_and_confirm(client, *, headers=OP, **over) -> dict:
    stored = await client.post("/api/amaze/uploads", params={"filename": DEMO.name}, headers={**headers, **OCTET},
                               content=DEMO.read_bytes())
    assert stored.status_code == 200, stored.text
    confirmed = await client.post(f"/api/amaze/uploads/{stored.json()['upload_id']}/confirm", headers=headers,
                                  json={**CONFIRM, **over})
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def _written(w, feed_id) -> tuple[int, int]:
    """(registration versions of the feed, onboarding events): what a refused request must leave as it was."""
    w.session.rollback()
    registrations = w.session.scalar(text("SELECT count(*) FROM feed_registrations WHERE feed_id = :f"), {"f": feed_id})
    events = w.session.scalar(text("SELECT count(*) FROM onboarding_events"))
    w.session.rollback()
    return registrations, events


async def call(w, method, path, *, user=OP, expect=200, **kw):
    response = await w.client.request(method, f"{BASE}{path}", headers=user, **kw)
    assert response.status_code == expect, response.text
    return response.json()


async def workspace_of(w, oid) -> dict:
    return await call(w, "GET", f"/{oid}")


async def settle(w, oid) -> int:
    """Decide through the API every item of the step that only a person can (a tie, a conflict)."""
    decided = 0
    for item in (await workspace_of(w, oid))["items"]:
        if not item["needs_decision"]:
            continue
        if item["kind"] == "mapping":
            choice = item["payload"]["candidates"][0]
        elif item["kind"] == "glossary_element":
            choice = 0
        elif item["kind"] == "rule":  # task 5.3b: it flags rows of this file that are valid; the operator rejects it
            await call(w, "POST", f"/{oid}/items/{item['proposal_id']}/decision",
                       json={"decision": "rejected", "note": "It flags valid rows of this file."})
            decided += 1
            continue
        else:
            continue
        await call(w, "POST", f"/{oid}/items/{item['proposal_id']}/decision", json={"decision": "approved",
                                                                                      "choice": choice})
        decided += 1
    return decided


async def run_the_pipeline(client, feed_id) -> dict:
    """Phase 7.17: the go-live needs a passed pipeline run of the code it pins (to a temporary repository, see conftest)."""
    response = await client.post(f"/api/amaze/feeds/{feed_id}/pipeline-runs", headers=OP)
    assert response.status_code == 200 and response.json()["outcome"] == "passed", response.text
    return response.json()


async def advance_to(w, oid, step) -> None:
    """Approve & next, with the worker between, until `step` is current and its job has answered."""
    w.worker.drain()
    while (await workspace_of(w, oid))["current_step"] != step:
        await settle(w, oid)
        await call(w, "POST", f"/{oid}/approve", json={})
        w.worker.drain()
    await settle(w, oid)


async def test_the_security_master_goes_from_upload_to_product_ready_through_the_api(world):
    w = world
    confirmed = await upload_and_confirm(w.client)
    oid = confirmed["onboarding_id"]
    assert confirmed["outcome"] == "uploaded" and confirmed["feed_code"] == "syn_security_master"
    assert w.app.state.onboarding_jobs.kicked.wait(5)  # the worker was woken after the commit

    # the list
    [row] = await call(w, "GET", "")
    assert (row["id"], row["feed_code"], row["feed_name"], row["current_step"], row["status"]) == (
        oid, "syn_security_master", "Security master", "profile", "active")
    assert row["step_states"]["upload"] == "done" and row["step_states"]["profile"] == "agent_working"
    assert (row["created_by"], row["model_calls"], row["model_call_cap"]) == ("dev-operator", 0, 60)
    assert row["created_at"] and row["updated_at"]

    # the profile job has not run: nothing to approve (409, never 500)
    waiting = await workspace_of(w, oid)
    assert waiting["current_step"] == "profile" and waiting["steps"][1]["state"] == "agent_working"
    assert waiting["job"]["status"] == "queued" and not waiting["can_approve"]
    await call(w, "POST", f"/{oid}/approve", json={}, expect=409)
    w.session.rollback()

    # the profile job: the summary with the issues of the file and the key column
    ran = w.worker.drain()
    assert [(r.step, r.status) for r in ran] == [("profile", "done")]
    ws = await workspace_of(w, oid)
    [item] = [i for i in ws["items"] if i["kind"] == "profile_summary"]  # T1: the agent's actions come beside it
    assert item["pre_accepted"] and not item["needs_decision"]
    assert {i["kind"] for i in ws["items"]} == {"profile_summary", "profile_action"}
    assert item["payload"]["key_columns"] == ["TICKER_NAME"]
    for column in ("COUNTRY", "REGION", "F_PERIOD", "RECORD_DATE"):
        assert column in item["payload"]["summary"], column
    assert item["payload"]["recommended_outcome"] == "meets_with_gaps"
    assert ws["agent_notes"][0]["agent"] == "ProfilerAgent" and ws["model_calls"] == 1 and ws["can_approve"]
    assert ws["visibility"] is None and ws["viewed_step"] == "profile"  # not registered yet
    assert item["reason"].startswith(item["payload"]["summary"]) and item["payload"]["key_reason"] in item["reason"]

    # "Inputs it saw", from the database only: the profile's counts, issues and key (task 7.3a)
    seen = ws["inputs"]
    assert seen["row_count"] == 70 and seen["column_count"] == len(seen["columns"]) > 10
    assert {"COUNTRY", "REGION", "F_PERIOD", "RECORD_DATE"} <= {c for i in seen["issues"] for c in i["columns"]}
    assert seen["key_columns"] == ["TICKER_NAME"]
    assert seen["duplicate_key_count"] == (item["payload"]["duplicate_key"] or {"extra_rows": 0})["extra_rows"]
    assert seen["candidate_counts"] == {} and all(f["value"] and "0 of 0" not in f["value"] for f in seen["facts"])

    # a user who is not in the backend is refused, and so is no one
    refused = await w.client.get(f"{BASE}/{oid}", headers=REQUESTER)
    assert refused.status_code == 403
    assert (await w.client.get(f"{BASE}/{oid}")).status_code == 401
    assert (await w.client.get(BASE, headers=REQUESTER)).status_code == 403

    # Approve & next registers the feed (visibility open) and enters the glossary
    advance = await call(w, "POST", f"/{oid}/approve", json={"visibility": "open"})
    assert advance["advance"]["moved"] and advance["advance"]["current_step"] == "glossary"
    assert advance["workspace"]["current_step"] == "glossary"

    # glossary, mapping: one item decided on its own through the item route, the rest by Approve & next
    await advance_to(w, oid, "mapping")
    on_mapping = await workspace_of(w, oid)
    counts = on_mapping["inputs"]["candidate_counts"]  # per column, how many candidate elements the agent was given
    assert set(counts) == {i["column"] for i in on_mapping["items"]} and all(0 <= n <= 10 for n in counts.values())
    assert any(n > 0 for n in counts.values())
    assert on_mapping["visibility"] == "open" and on_mapping["viewed_step"] == "mapping"

    # a visibility given at any step other than profile or go-live is refused (409), and nothing is written
    before = _written(w, confirmed["feed_id"])
    refused = await w.client.post(f"{BASE}/{oid}/approve", headers=OP, json={"visibility": "restricted"})
    assert refused.status_code == 409 and "chosen at go-live" in refused.json()["detail"], refused.text
    w.session.rollback()
    assert _written(w, confirmed["feed_id"]) == before
    assert (await workspace_of(w, oid))["current_step"] == "mapping"

    # any earlier step can be viewed, read only; a step never run has no items; an unknown step is 422
    earlier = await call(w, "GET", f"/{oid}", params={"step": "profile"})
    assert earlier["current_step"] == "mapping" and earlier["viewed_step"] == "profile"
    assert earlier["items"][0]["kind"] == "profile_summary" and {i["kind"] for i in earlier["items"]} == {
        "profile_summary", "profile_action"} and earlier["agent_notes"]
    assert earlier["can_approve"] is False and earlier["blocked_by"] == "Viewing an earlier step."
    assert earlier["inputs"]["key_columns"] == ["TICKER_NAME"] and earlier["inputs"]["issues"]
    later = await call(w, "GET", f"/{oid}", params={"step": "rules"})
    assert later["viewed_step"] == "rules" and later["items"] == [] and later["can_approve"] is False
    await call(w, "GET", f"/{oid}", params={"step": "no_such_step"}, expect=422)
    w.session.rollback()
    [first, *_] = [i for i in on_mapping["items"] if i["status"] == "pending"]
    choice = first["payload"]["recommended"] or first["payload"]["candidates"][0]
    decided = await call(w, "POST", f"/{oid}/items/{first['proposal_id']}/decision",
                         json={"decision": "approved", "choice": choice, "note": "Looks right."})
    assert decided["result"]["outcome"] == "mapped"
    again = next(i for i in decided["workspace"]["items"] if i["proposal_id"] == first["proposal_id"])
    assert again["status"] == "approved"
    assert first["decided_by"] is None and first["decided_at"] is None  # pending: nobody has decided yet
    assert again["decided_by"] == "dev-operator" and again["decided_at"]  # the latest approval decision (task 7.3c)
    await call(w, "POST", f"/{oid}/items/{first['proposal_id']}/decision", json={"decision": "approved"}, expect=409)
    w.session.rollback()

    # T7, mapping: reject the agent's item for a column, map it by hand to an element the agent never offered; a second
    # item for the column is 409; a column the file lacks is 422; the item says it was added by a person (no model call)
    target = next(i for i in on_mapping["items"] if i["status"] == "pending" and i["proposal_id"] != first["proposal_id"])
    await call(w, "POST", f"/{oid}/items/{target['proposal_id']}/decision",
               json={"decision": "rejected", "note": "I will map it myself."})
    free = w.session.execute(text("SELECT key FROM glossary_elements g WHERE data_type = 'string' AND NOT EXISTS "
                                  "(SELECT 1 FROM mappings m WHERE m.element_key = g.key) ORDER BY key")).scalars().first()
    by_hand = {"kind": "mapping", "source_field": target["column"], "element_key": free, "reason": "The desk says so."}
    logged = w.session.execute(text("SELECT count(*) FROM proposal_log")).scalar_one()
    added = await call(w, "POST", f"/{oid}/items", json=by_hand)
    assert (added["proposal"]["origin"], added["proposal"]["author"], added["proposal"]["proposal_log_id"]) == (
        "manual", "dev-operator", None)
    assert w.session.execute(text("SELECT count(*) FROM proposal_log")).scalar_one() == logged
    mine = next(i for i in added["workspace"]["items"] if i["proposal_id"] == added["proposal"]["id"])
    assert (mine["origin"], mine["author"], mine["pre_accepted"], mine["needs_decision"]) == (
        "manual", "dev-operator", True, False)
    assert mine["proposal_id"] not in {n["proposal_id"] for n in added["workspace"]["agent_notes"]}  # no agent's note
    await call(w, "POST", f"/{oid}/items", json=by_hand, expect=409)
    w.session.rollback()
    await call(w, "POST", f"/{oid}/items", json={**by_hand, "source_field": "not_a_column"}, expect=422)
    w.session.rollback()
    await call(w, "POST", f"/{oid}/items", json={"kind": "mapping", "source_field": "x"}, expect=422)  # neither one
    w.session.rollback()
    await call(w, "POST", f"/{oid}/items", json={**by_hand, "kind": "rule"}, expect=422)  # not a valid rule item
    w.session.rollback()
    await advance_to(w, oid, "transformations")

    # transformations: the agent doubts one item; Approve & next says which (409 decision_needed with the items)
    ws = await workspace_of(w, oid)
    steps_by_column = {i["column"]: [s["op"] for s in i["payload"]["steps"]] for i in ws["items"]}
    assert steps_by_column["COUNTRY"] == ["placeholder_to_null"] and steps_by_column["RECORD_DATE"] == ["parse_date"]
    [doubted] = [i for i in ws["items"] if i["needs_decision"]]
    refused = await w.client.post(f"{BASE}/{oid}/approve", headers=OP, json={})
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["outcome"] == "decision_needed" and "decision" in detail["message"]
    assert [i["proposal_id"] for i in detail["items"]] == [doubted["proposal_id"]]
    w.session.rollback()

    # the key policy (step 10)
    policy = await call(w, "PUT", f"/{oid}/key-policy", json={
        "policy": "keep_last", "key_columns": ["TICKER_NAME"], "reason": "The last row is the right one."})
    assert policy["key_policy"]["policy"] == "keep_last" and policy["workspace"]["key_policy"]["policy"] == "keep_last"
    await call(w, "PUT", f"/{oid}/key-policy", user=REQUESTER, expect=403, json={
        "policy": "keep_last", "key_columns": ["TICKER_NAME"], "reason": "No."})

    # Back to mapping: the later steps are stale; forward again; the pending transformations are rejected by system
    went_back = await call(w, "POST", f"/{oid}/back", json={"to_step": "mapping"})
    assert went_back["current_step"] == "mapping" and went_back["session"]["step_states"]["transformations"] == "stale"
    await call(w, "POST", f"/{oid}/back", json={"to_step": "upload"}, expect=409)
    w.session.rollback()
    await advance_to(w, oid, "transformations")
    [doubted] = [i for i in (await workspace_of(w, oid))["items"] if i["needs_decision"]]

    # the edit: the operator changes the steps; the edited proposal is accepted by the person who edited it
    payload = {**doubted["payload"], "steps": [{"op": "trim"}, *doubted["payload"]["steps"]]}
    edited = await call(w, "POST", f"/{oid}/items/{doubted['proposal_id']}/edit", json={"payload": payload})
    assert edited["proposal"]["edited_by"] == "dev-operator" and edited["proposal"]["edited_from"] == doubted[
        "proposal_id"]
    assert edited["workspace"]["can_approve"]
    mine = next(i for i in edited["workspace"]["items"] if i["proposal_id"] == edited["proposal"]["id"])
    assert mine["edited"] and mine["pre_accepted"] and not mine["needs_decision"]
    await call(w, "POST", f"/{oid}/items/{doubted['proposal_id']}/edit", json={"payload": payload}, expect=409)
    w.session.rollback()
    await call(w, "POST", f"/{oid}/items/{mine['proposal_id']}/edit", expect=422,
               json={"payload": {**payload, "steps": [{"op": "no_such_operation"}]}})
    w.session.rollback()
    await call(w, "POST", f"/{oid}/items/{UUID(int=1)}/edit", expect=404, json={"payload": payload})
    w.session.rollback()

    # T7, rules: a manual rule that fails its back-test is a named 422, and nothing is written; one that passes is
    # added; an agent's rule is edited (back-tested again) and another is rejected
    await advance_to(w, oid, "rules")
    element = w.session.execute(text("SELECT element_key FROM mappings ORDER BY created_at LIMIT 1")).scalar_one()
    rule = {"kind": "rule", "rule_key": "api_probe_rule", "description": "Nothing is ever flagged.",
            "sql": "SELECT _row FROM curated WHERE 1 = 0", "element_keys": [element]}
    rules_before = w.session.execute(text("SELECT count(*) FROM proposals WHERE kind = 'rule'")).scalar_one()
    refusal = await call(w, "POST", f"/{oid}/items", json={**rule, "sql": "DROP TABLE curated"}, expect=422)
    assert refusal["detail"]
    w.session.rollback()
    assert w.session.execute(text("SELECT count(*) FROM proposals WHERE kind = 'rule'")).scalar_one() == rules_before
    manual_rule = await call(w, "POST", f"/{oid}/items", json=rule)
    assert manual_rule["proposal"]["origin"] == "manual" and manual_rule["proposal"]["payload"]["backtest"]["status"]
    await call(w, "POST", f"/{oid}/items", json=rule, expect=409)
    w.session.rollback()
    agent_rules = [i for i in (await workspace_of(w, oid))["items"] if i["kind"] == "rule" and i["status"] == "pending"
                   and i["origin"] == "agent"]
    assert len(agent_rules) >= 2
    reworded = await call(w, "POST", f"/{oid}/items/{agent_rules[0]['proposal_id']}/edit", json={
        "payload": {**agent_rules[0]["payload"], "description": "Reworded by the steward."}})
    assert reworded["proposal"]["edited_by"] == "dev-operator" and reworded["proposal"]["origin"] == "agent"
    await call(w, "POST", f"/{oid}/items/{reworded['proposal']['id']}/edit", expect=422, json={
        "payload": {**reworded["proposal"]["payload"], "sql": "DROP TABLE curated"}})
    w.session.rollback()
    await call(w, "POST", f"/{oid}/items/{agent_rules[1]['proposal_id']}/decision",
               json={"decision": "rejected", "note": "Wrong for this data."})

    # rules, go-live (the owner is the operator); then the first load
    await advance_to(w, oid, "go_live")
    at_go_live = await workspace_of(w, oid)
    assert at_go_live["visibility"] == "open" and any(f["label"] == "Test load" for f in at_go_live["inputs"]["facts"])
    # T2: the completion report, in the go-live step and as its own read-only route
    report = await w.client.get(f"/api/amaze/feeds/{confirmed['feed_id']}/completion", headers=OP)
    assert report.status_code == 200, report.text
    assert report.json() == at_go_live["inputs"]["completion"]
    assert report.json()["mappings"]["done"] > 0 and report.json()["items"]["manual"] >= 2  # the mapping and the rule
    assert (await w.client.get(f"/api/amaze/feeds/{UUID(int=9)}/completion", headers=OP)).status_code == 404
    assert (await w.client.get(f"/api/amaze/feeds/{confirmed['feed_id']}/completion", headers=REQUESTER)).status_code in (200, 403)
    w.session.rollback()
    assert all(f["value"] and "0 of 0" not in f["value"] for f in at_go_live["inputs"]["facts"])
    # the owner chooses the visibility here: a next registration version, then the go-live as before
    await run_the_pipeline(w.client, confirmed["feed_id"])  # 7.17: the go-live needs a passed run of the code it pins
    await call(w, "POST", f"/{oid}/approve", json={"visibility": "restricted"})
    assert (await workspace_of(w, oid))["current_step"] == "first_load"
    assert (await workspace_of(w, oid))["visibility"] == "restricted"
    await call(w, "POST", f"/{oid}/back", json={"to_step": "go_live"}, expect=409)  # go-live is approved: pinned
    w.session.rollback()
    # a visibility sent after the go-live item is approved is refused (409) and writes nothing
    before = _written(w, confirmed["feed_id"])
    refused = await w.client.post(f"{BASE}/{oid}/approve", headers=OP, json={"visibility": "open"})
    assert refused.status_code == 409, refused.text
    w.session.rollback()
    assert _written(w, confirmed["feed_id"]) == before
    assert (await workspace_of(w, oid))["visibility"] == "restricted"
    ran = w.worker.drain()
    assert [(r.step, r.status) for r in ran] == [("first_load", "done")], [r.detail for r in ran]
    done = await workspace_of(w, oid)
    assert done["session"]["status"] == "done" and done["current_step"] == "product_ready"
    product = ProductRepository(w.session).for_feed(UUID(confirmed["feed_id"]))
    assert product is not None and product.visibility == "restricted"  # the product has the latest registration's
    # T3: the go-live approval stored code version 1 and pinned it; the files are what the engine runs
    feed_url = f"/api/amaze/feeds/{confirmed['feed_id']}/code"
    versions = (await w.client.get(feed_url, headers=OP)).json()
    assert [v["version"] for v in versions] == [1] and versions[0]["approved_by"] == "dev-operator"
    assert {"load.sql", "curated.sql", "staging_ddl.sql", "README.md"} <= set(versions[0]["paths"])
    detail = (await w.client.get(f"{feed_url}/1", headers=OP)).json()
    preview = (await w.client.get(f"{feed_url}/preview", headers=OP)).json()
    assert detail["files"] == preview["files"] and preview["differs_from_latest"] is False
    assert (await w.client.get(f"{feed_url}/diff", params={"from": 1}, headers=OP)).json()["files"] == []
    assert (await w.client.get(f"{feed_url}/9", headers=OP)).status_code == 404
    assert (await w.client.get(f"{feed_url}/diff", params={"from": 1, "to": 9}, headers=OP)).status_code == 404
    w.session.rollback()
    loaded = {f["label"]: f["value"] for f in (await call(w, "GET", f"/{oid}", params={"step": "first_load"}))[
        "inputs"]["facts"]}
    assert loaded["Load"] == "released" and loaded["Outcome"] == "released" and loaded["Product"] == product.code
    assert float(loaded["Trust score"]) >= 0 and int(loaded["Rows loaded"]) > 0 and "Rows flagged" in loaded
    assert int(loaded["Rows flagged"]) <= int(loaded["Rows loaded"])
    await call(w, "POST", f"/{oid}/steps/product_ready/enter", expect=409)
    w.session.rollback()

    # the timeline: the events, the proposals with their logged calls, the edit with its editor, the approvals
    trail = await call(w, "GET", f"/{oid}/timeline")
    assert {t["kind"] for t in trail} == {"event", "proposal", "edit", "approval"}
    [edit] = [t for t in trail if t["kind"] == "edit" and t["proposal_id"] == edited["proposal"]["id"]]
    assert {t["detail"]["edited_by"] for t in trail if t["kind"] == "edit"} == {"dev-operator"}  # also T7's rule edit
    assert edit["actor"] == "dev-operator" and edit["detail"]["edited_by"] == "dev-operator"
    assert edit["proposal_id"] == edited["proposal"]["id"]
    approved_events = [t for t in trail if t["kind"] == "event" and t["title"] == "approved"]
    assert any(t["proposal_id"] == first["proposal_id"] and t["actor"] == "dev-operator" for t in approved_events)
    assert {"entered", "agent_done", "pre_accepted", "edited", "approved", "back", "stale", "item_added"} <= {
        t["title"] for t in trail if t["kind"] == "event"}
    added_by = [t for t in trail if t["kind"] == "proposal" and " added by " in t["title"]]  # T7: no logged call to show
    assert {t["title"].split(" added by ")[0] for t in added_by} == {"mapping", "rule"}
    assert all(t["actor"] == "dev-operator" and t["detail"]["origin"] == "manual" and "model" not in t["detail"]
               for t in added_by)
    final = await call(w, "GET", "")
    assert final[0]["status"] == "done" and final[0]["model_calls"] > 5


async def test_an_unknown_onboarding_is_404_and_a_step_that_is_not_current_is_409(world):
    w = world
    await call(w, "GET", f"/{UUID(int=7)}", expect=404)
    await call(w, "GET", f"/{UUID(int=7)}/timeline", expect=404)
    await call(w, "POST", f"/{UUID(int=7)}/approve", json={}, expect=404)
    w.session.rollback()
    oid = (await upload_and_confirm(w.client))["onboarding_id"]
    await call(w, "POST", f"/{oid}/steps/mapping/enter", expect=409)  # not the current step
    w.session.rollback()
    await call(w, "POST", f"/{oid}/steps/profile/enter", expect=409)  # its agent is working
    w.session.rollback()
    await call(w, "POST", f"/{oid}/steps/not_a_step/enter", expect=422)


async def test_confirming_an_upload_starts_the_onboarding_in_the_same_transaction(world):
    w = world
    confirmed = await upload_and_confirm(w.client)
    session = OnboardingRepository(w.session).get_by_feed(UUID(confirmed["feed_id"]))
    assert str(session.id) == confirmed["onboarding_id"] and session.current_step == "profile"
    job = OnboardingJobRepository(w.session).active_for_session(session.id)
    assert (job.step, job.status) == ("profile", "queued")


async def test_a_confirmation_that_is_refused_starts_no_onboarding(world):
    w = world
    await upload_and_confirm(w.client)
    stored = await w.client.post("/api/amaze/uploads", params={"filename": DEMO.name}, headers={**OP, **OCTET},
                                 content=DEMO.read_bytes())
    again = await w.client.post(f"/api/amaze/uploads/{stored.json()['upload_id']}/confirm", headers=OP, json=CONFIRM)
    assert again.status_code == 409  # the feed code is taken
    assert len(OnboardingRepository(w.session).list()) == 1


# ---------------------------------------------------------------- the two workers never run one glossary run twice


async def registered_at_glossary(w) -> UUID:
    """The path as far as Approve & next on the profile step: the feed is registered, a glossary run is queued and
    the glossary job waits. Returns the onboarding id."""
    oid = (await upload_and_confirm(w.client))["onboarding_id"]
    w.worker.drain()
    await call(w, "POST", f"/{oid}/approve", json={})
    return oid


def glossary_workers(w, provider):
    notes = LocalBlobStore(w.settings.dropzone_root.parent / "other-agent-blob")
    return GlossaryRuns(session_factory=w.sf, agent_factory=lambda: GlossaryBuilderAgent(provider=provider,
                                                                                        blob_store=notes),
                        owners=lambda: ["dev-glossary-owner"])


async def test_a_run_the_onboarding_job_has_claimed_is_not_run_by_the_glossary_worker(world, tmp_path):
    w = world
    oid = await registered_at_glossary(w)
    other = Counting()
    taken = {}
    thinking = During(lambda: taken.setdefault("by_the_other_worker", glossary_workers(w, other).drain()))
    ctx = make_context(w.settings, w.blobs, w.dropzone, thinking, LocalBlobStore(tmp_path / "n"), w.sf)
    ran = OnboardingJobs(session_factory=w.sf, context_factory=lambda: ctx).drain()
    assert [(r.step, r.status) for r in ran] == [("glossary", "done")], [r.detail for r in ran]
    assert thinking.calls == 1  # the run was run once, by the onboarding job
    assert taken["by_the_other_worker"] == {} and other.calls == 0
    assert (await workspace_of(w, oid))["steps"][2]["state"] in ("ready", "needs_decision")


async def test_a_run_the_glossary_worker_has_claimed_is_not_run_by_the_onboarding_job(world, tmp_path):
    w = world
    oid = await registered_at_glossary(w)
    taken = {}

    def meanwhile():
        taken["by_the_onboarding_job"] = w.worker.drain()

    results = glossary_workers(w, During(meanwhile)).drain()
    assert list(results.values()) == ["done"]  # the glossary worker ran the run
    [busy] = taken["by_the_onboarding_job"]
    assert (busy.step, busy.status) == ("glossary", "failed") and "enter the step again" in busy.detail
    # nothing was run twice: entering the step again finds the run done and shows its proposals
    await call(w, "POST", f"/{oid}/steps/glossary/enter")
    ran = w.worker.drain()
    assert [(r.step, r.status, r.model_calls) for r in ran] == [("glossary", "done", 0)]
    assert (await workspace_of(w, oid))["steps"][2]["state"] in ("ready", "needs_decision")


# ---------------------------------------------------------------- the worker, its kick and its lease


def test_a_kick_names_the_onboarding_job_and_runs_the_drain():
    class Scheduler:
        def __init__(self):
            self.calls = []

        def run_soon(self, job_id, func, *, delay_seconds=0.0):
            self.calls.append((job_id, func, delay_seconds))

    class Jobs:
        def __init__(self):
            self.drained, self.waits = 0, [None]

        def drain(self):
            self.drained += 1
            return []

        def lease_wait(self):
            return self.waits.pop(0)

    scheduler, jobs = Scheduler(), Jobs()
    kick_onboarding_jobs(scheduler, jobs)
    [(job_id, func, delay)] = scheduler.calls
    assert (job_id, delay) == ("onboarding-jobs", 0.0)
    func()
    assert jobs.drained == 1 and len(scheduler.calls) == 1  # no live lease: no later kick
    jobs.waits = [30.0]
    func()
    assert len(scheduler.calls) == 2 and scheduler.calls[1][0] == "onboarding-jobs"
    assert 30.0 < scheduler.calls[1][2] < 35.0  # one more kick just after the lease passes


async def test_the_lease_wait_is_the_time_to_the_earliest_live_lease(world):
    w = world
    assert w.worker.lease_wait() is None
    await upload_and_confirm(w.client)
    assert w.worker.lease_wait() is None  # queued, not running
    job = OnboardingJobRepository(w.session).claim(lease_seconds=300)
    w.session.commit()
    assert job is not None and 250 < w.worker.lease_wait() <= 300


async def test_a_rejected_glossary_draft_reruns_the_mapping_drafts_built_on_it_through_the_api(world):
    """Phase 7.13: Run all agents, the profile click, the chain; then one rejection through the item decision route.
    The mapping drafts built on it are rejected by system and the mapping step drafts again; a draft approved before
    the draft it is built on is a 409."""
    w = world
    oid = (await upload_and_confirm(w.client))["onboarding_id"]
    await call(w, "POST", f"/{oid}/run-all")
    w.worker.drain()
    await call(w, "POST", f"/{oid}/approve", json={})  # the profile: the one click
    w.worker.drain()
    proposals = ProposalRepository(w.session)
    feed_id = OnboardingRepository(w.session).get(UUID(oid)).feed_id
    mapping = next(m for m in proposals.list_for_feed(feed_id, "mapping", "pending") if proposals.bases(m.id))
    base = proposals.bases(mapping.id)[0]
    w.session.rollback()

    await call(w, "POST", f"/{oid}/items/{mapping.id}/decision", json={"decision": "approved"}, expect=409)
    await call(w, "POST", f"/{oid}/items/{base}/decision", json={"decision": "rejected", "note": "Not an element."})
    assert proposals.get(mapping.id).status == "rejected"
    assert proposals.approvals(mapping.id)[0].decided_by == "system"
    w.session.rollback()
    w.worker.drain()
    states = (await workspace_of(w, oid))["session"]["step_states"]
    assert all(states[s] in ("ready", "needs_decision") for s in ("glossary", "mapping", "transformations", "rules"))


# ---------------------------------------------------------------- the app's real sessions, on a schema of its own


class Real:
    """The API and the worker on a schema of amaze_test (rows really commit), the mock behind both."""

    def __init__(self, engine, tmp_path, provider=None):
        self.engine, self.tmp_path = engine, tmp_path
        self.blobs, self.dropzone = LocalBlobStore(tmp_path / "blob"), tmp_path / "dropzone"
        self.settings = Settings(_env_file=None, dropzone_root=self.dropzone)
        self.provider = provider or MockProvider()
        self.sf = get_sessionmaker(engine)
        with self.sf() as session:
            FourEyesRepository(session).set(False, set_by="test", reason="Synthetic.")
            session.commit()
        self.ctx = make_context(self.settings, self.blobs, self.dropzone, self.provider,
                                LocalBlobStore(tmp_path / "agent-blob"), self.sf)
        self.worker = OnboardingJobs(session_factory=self.sf, context_factory=lambda: self.ctx)

    @asynccontextmanager
    async def app(self, onboarding_jobs=None):
        from apps.api.deps import get_blob_store_port, get_llm_provider_port
        from apps.api.main import app

        async with running_app(engine=self.engine, settings=self.settings, onboarding_jobs=onboarding_jobs) as client:
            app.dependency_overrides[get_blob_store_port] = lambda: self.blobs
            app.dependency_overrides[get_llm_provider_port] = lambda: self.provider
            self.client = client
            yield self

    async def call(self, method, path, *, user=OP, expect=200, **kw):
        response = await self.client.request(method, f"{BASE}{path}", headers=user, **kw)
        assert response.status_code == expect, response.text
        return response.json()


@pytest.fixture
def real(own_schema, tmp_path):  # noqa: F811
    return Real(own_schema, tmp_path)


async def test_every_committing_workspace_endpoint_commits_what_it_wrote(real):
    """One commit-visibility check per committing endpoint (confirm, enter, approve, key policy, item decision, item
    edit, back), on ONE schema and one walk of the flow (phase 7.8: the shared setup that keeps the suite within its
    time ceiling; until then each endpoint had its own test, schema and walk). Each check reads the database through a
    connection of its own after the endpoint answered, so only committed rows are seen."""
    async with real.app() as r:
        engine = r.engine

        # confirm: the session and its first job
        confirmed = await upload_and_confirm(r.client)
        oid = confirmed["onboarding_id"]
        assert committed(engine, "SELECT id::text, current_step, status, created_by FROM onboarding_sessions") == [
            (oid, "profile", "active", "dev-operator")]
        assert committed(engine, "SELECT step, status FROM onboarding_jobs") == [("profile", "queued")]
        assert committed(engine, "SELECT event FROM onboarding_events ORDER BY at") == [("entered",), ("agent_queued",)]

        # enter: the queued job
        r.worker.drain()
        entered = await r.call("POST", f"/{oid}/steps/profile/enter")
        assert entered["steps"][1]["state"] == "agent_working"
        assert committed(engine, "SELECT step, status FROM onboarding_jobs ORDER BY created_at") == [
            ("profile", "done"), ("profile", "queued")]
        assert committed(engine, "SELECT step_states->>'profile' FROM onboarding_sessions") == [("agent_working",)]
        r.worker.drain()

        # approve: the decisions, the registration and the next job
        await r.call("POST", f"/{oid}/approve", json={"visibility": "restricted"})
        assert committed(engine, "SELECT current_step, step_states->>'profile' FROM onboarding_sessions") == [
            ("glossary", "done")]
        assert committed(engine, "SELECT stage, owner_id FROM feeds") == [("registered", "dev-operator")]
        assert committed(engine, "SELECT visibility FROM feed_registrations") == [("restricted",)]
        assert committed(engine, "SELECT step, status FROM onboarding_jobs WHERE status = 'queued'") == [
            ("glossary", "queued")]
        # the summary and each action the agent proposed (T1) were approved
        assert committed(engine, "SELECT count(*) FROM onboarding_events WHERE event = 'approved'")[0][0] >= 2
        assert committed(engine, "SELECT count(*) FROM proposals WHERE kind = 'profile_action' AND status <> 'approved'") == [
            (0,)]

        # the key policy: the next version
        await r.call("PUT", f"/{oid}/key-policy", json={"policy": "hold", "key_columns": ["TICKER_NAME"],
                                                        "reason": "A repeated id must stop the load."})
        assert committed(engine, "SELECT version, policy, key_columns::text, set_by FROM feed_key_policies") == [
            (1, "hold", "{TICKER_NAME}", "dev-operator")]

        # on to mapping (the glossary job, its decisions, Approve & next, the mapping job)
        r.worker.drain()
        for item in (await r.call("GET", f"/{oid}"))["items"]:
            if item["needs_decision"]:
                await r.call("POST", f"/{oid}/items/{item['proposal_id']}/decision",
                             json={"decision": "approved", "choice": 0})
        await r.call("POST", f"/{oid}/approve", json={})
        r.worker.drain()
        pending = [i for i in (await r.call("GET", f"/{oid}"))["items"] if i["status"] == "pending"]
        assert (await r.call("GET", f"/{oid}"))["current_step"] == "mapping" and len(pending) >= 2

        # an item decision: the mapping and its event
        item = pending[0]
        choice = item["payload"]["recommended"] or item["payload"]["candidates"][0]
        await r.call("POST", f"/{oid}/items/{item['proposal_id']}/decision",
                     json={"decision": "approved", "choice": choice})
        assert committed(engine, "SELECT decision, decided_by FROM approvals WHERE proposal_id = :p",
                         p=item["proposal_id"]) == [("approved", "dev-operator")]
        assert committed(engine, "SELECT count(*) FROM mappings WHERE proposal_id = :p", p=item["proposal_id"]) == [
            (1,)]
        assert committed(engine, "SELECT actor FROM onboarding_events WHERE event = 'approved' AND proposal_id = :p",
                         p=item["proposal_id"]) == [("dev-operator",)]

        # an item edit: the new version and its events
        item = pending[1]
        edited = await r.call("POST", f"/{oid}/items/{item['proposal_id']}/edit",
                              json={"payload": {**item["payload"], "reason": "Checked by the operator."}})
        new_id = edited["proposal"]["id"]
        assert committed(engine, "SELECT status, edited_by, edited_from::text = :old FROM proposals WHERE id = :new",
                         old=item["proposal_id"], new=new_id) == [("pending", "dev-operator", True)]
        assert committed(engine, "SELECT status FROM proposals WHERE id = :old", old=item["proposal_id"]) == [
            ("superseded",)]
        assert committed(engine, "SELECT event, actor FROM onboarding_events WHERE proposal_id = :p ORDER BY at",
                         p=new_id) == [("edited", "dev-operator"), ("pre_accepted", "dev-operator")]

        # a manual item (T7): the proposal with its author and no logged call, and its two events
        item = pending[2]
        await r.call("POST", f"/{oid}/items/{item['proposal_id']}/decision",
                     json={"decision": "rejected", "note": "I will map it myself."})
        free = committed(engine, "SELECT key FROM glossary_elements g WHERE data_type = 'string' AND NOT EXISTS "
                                 "(SELECT 1 FROM mappings m WHERE m.element_key = g.key) ORDER BY key LIMIT 1")[0][0]
        added = await r.call("POST", f"/{oid}/items", json={
            "kind": "mapping", "source_field": item["column"], "element_key": free, "reason": "By hand."})
        new_id = added["proposal"]["id"]
        assert committed(engine, "SELECT origin, author, proposal_log_id IS NULL, status FROM proposals WHERE id = :p",
                         p=new_id) == [("manual", "dev-operator", True, "pending")]
        assert committed(engine, "SELECT event, actor FROM onboarding_events WHERE proposal_id = :p ORDER BY at",
                         p=new_id) == [("item_added", "dev-operator"), ("pre_accepted", "dev-operator")]

        # back: the stale steps and the new job
        went_back = await r.call("POST", f"/{oid}/back", json={"to_step": "glossary"})
        assert went_back["current_step"] == "glossary"
        assert committed(engine, "SELECT current_step, step_states->>'glossary', step_states->>'mapping' "
                                 "FROM onboarding_sessions") == [("glossary", "agent_working", "stale")]
        assert committed(engine, "SELECT step, status FROM onboarding_jobs WHERE status = 'queued'") == [
            ("glossary", "queued")]
        assert committed(engine, "SELECT count(*) FROM onboarding_events WHERE event = 'back'") == [(1,)]

        # run all agents (phase 7.13): the run mode and its event
        ran_all = await r.call("POST", f"/{oid}/run-all")
        assert ran_all["session"]["run_mode"] == "chain"
        assert committed(engine, "SELECT run_mode FROM onboarding_sessions") == [("chain",)]
        assert committed(engine, "SELECT step, actor FROM onboarding_events WHERE event = 'chain_started'") == [
            ("glossary", "dev-operator")]
        await r.call("POST", f"/{oid}/run-all", expect=409)  # one click, once

        # approve all confident (phase 7.13, T2): one approval row per item, by the person who clicked
        r.worker.drain()  # the glossary run, then the chain on its drafts
        chain = await r.call("GET", f"/{oid}/chain")
        assert [s["step"] for s in chain["steps"]] == ["glossary", "mapping", "transformations", "rules"]
        step = next(s["step"] for s in chain["steps"] if any(i["confident"] for i in s["items"]))
        done = await r.call("POST", f"/{oid}/chain/approve-confident", json={"step": step})
        assert done["approved"]
        assert committed(engine, "SELECT count(*) FROM approvals WHERE decided_by = 'dev-operator' AND proposal_id IN "
                                 "(SELECT CAST(x AS uuid) FROM unnest(CAST(:ids AS text[])) AS x)",
                         ids=done["approved"]) == [(len(done["approved"]),)]


# ---------------------------------------------------------------- resume after a restart


async def wait_for(engine, sql, want, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if committed(engine, sql) == want:
            return
        await asyncio.sleep(0.1)
    assert committed(engine, sql) == want


async def test_a_queued_job_is_taken_when_the_app_starts(real):
    async with real.app() as r:  # the first process: a confirmation queues the profile job, nobody runs it
        oid = (await upload_and_confirm(r.client))["onboarding_id"]
    assert committed(real.engine, "SELECT status FROM onboarding_jobs") == [("queued",)]
    async with real.app(onboarding_jobs=real.worker):  # the next process: its start-up kick takes the job
        await wait_for(real.engine, "SELECT status FROM onboarding_jobs", [("done",)])
        await wait_for(real.engine, "SELECT step_states->>'profile' FROM onboarding_sessions", [("ready",)])
    assert committed(real.engine, "SELECT id::text FROM onboarding_sessions") == [(oid,)]


async def test_a_running_job_whose_lease_passed_is_taken_when_the_app_starts(real):
    async with real.app() as r:
        await upload_and_confirm(r.client)
    with real.sf() as session:  # a process that died: the job is running with a lease of one second
        assert OnboardingJobRepository(session).claim(lease_seconds=1) is not None
        session.commit()
    await asyncio.sleep(1.3)
    async with real.app(onboarding_jobs=real.worker):
        await wait_for(real.engine, "SELECT status, attempt FROM onboarding_jobs", [("done", 2)])


# ---------------------------------------------------------------- no connection is held during a model call


async def test_no_connection_is_held_during_an_onboarding_jobs_model_call(own_schema, tmp_path):  # noqa: F811
    seen = []
    thinking = During(lambda: seen.append(own_schema.pool.checkedout()))
    r = Real(own_schema, tmp_path, provider=thinking)
    async with r.app() as r:
        oid = (await upload_and_confirm(r.client))["onboarding_id"]
        ran = r.worker.drain()
        assert [(x.step, x.status) for x in ran] == [("profile", "done")]
        await r.call("POST", f"/{oid}/approve", json={})
        r.worker.drain()  # the glossary job's call
    assert seen and set(seen) == {0}, seen


V2_FILE = Path(__file__).resolve().parent.parent / "fixtures" / "sources" / "Synthetic_Security_Master_v2.xlsx"


async def test_the_drift_and_code_endpoints_commit_and_serve_committed_rows(real):
    """Phase 7.12 (T3, T4): POST drift/actions is the one committing endpoint here (read back on a connection of its own);
    GET drift, GET code, GET code/diff and the rows step 12 wrote are read by the real app from committed state, after a real
    onboarding, a go-live that stored code version 1, and a second delivery that drifts. One walk, one schema."""
    async with real.app() as r:
        from apps.api.deps import get_daily_fetch
        from apps.api.main import app

        app.dependency_overrides[get_daily_fetch] = lambda: r.ctx.load_now.__self__  # the job on this schema, not the default
        oid = (await upload_and_confirm(r.client))["onboarding_id"]
        feed = committed(r.engine, "SELECT id::text FROM feeds")[0][0]
        r.worker.drain()
        await advance_to(r, oid, "go_live")
        await run_the_pipeline(r.client, feed)
        await r.call("POST", f"/{oid}/approve", json={"visibility": "open"})  # the go-live: code version 1 is stored
        r.worker.drain()  # the first load
        base = f"/api/amaze/feeds/{feed}"

        listed = (await r.client.get(f"{base}/code", headers=OP)).json()
        assert [(v["version"], v["approved_by"]) for v in listed] == [(1, "dev-operator")]
        assert committed(r.engine, "SELECT version, approved_by FROM code_versions") == [(1, "dev-operator")]
        diff = (await r.client.get(f"{base}/code/diff", params={"from": 1}, headers=OP)).json()
        assert (diff["from_version"], diff["to_version"], diff["files"]) == (1, None, [])  # nothing changed since

        stored = await r.client.post(f"{base}/files", params={"filename": V2_FILE.name}, content=V2_FILE.read_bytes(),
                                     headers={**OP, **OCTET})
        assert stored.status_code == 200, stored.text
        loaded = await r.client.post(f"{base}/loads", json={}, headers=OP)
        assert loaded.status_code == 200, loaded.text

        drift = (await r.client.get(f"{base}/drift", headers=OP)).json()
        assert drift["report"]["new_columns"] == ["BEST_FCF"] and drift["report"]["order_changed"] is True
        written = {x[0] for x in committed(
            r.engine, "SELECT id::text FROM proposals WHERE kind = 'profile_action' AND origin = 'rule'")}
        assert {a["proposal_id"] for a in drift["actions"]} == written  # what the endpoint shows is what step 12 committed
        assert committed(r.engine, "SELECT count(*) FROM loads WHERE arrival_detail ? 'drift'") == [(1,)]

        added = await r.client.post(f"{base}/drift/actions", headers=OP, json={
            "action": "add_rule", "columns": ["BEST_FCF"], "reason": "A free cash flow estimate has a sign."})
        assert added.status_code == 200, added.text
        assert committed(r.engine, "SELECT origin, author, proposal_log_id IS NULL, status FROM proposals "
                                   "WHERE kind = 'profile_action' AND origin = 'manual'") == [
            ("manual", "dev-operator", True, "pending")]
        again = (await r.client.get(f"{base}/drift", headers=OP)).json()
        assert [a["origin"] for a in again["actions"]].count("manual") == 1
        refused = await r.client.post(f"{base}/drift/actions", headers=OP, json={
            "action": "add_rule", "columns": ["not_a_column"], "reason": "x"})
        assert refused.status_code == 422
        assert committed(r.engine, "SELECT count(*) FROM proposals WHERE origin = 'manual' AND kind = 'profile_action'") == [(1,)]
