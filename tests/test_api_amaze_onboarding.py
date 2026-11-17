"""Amaze API for steps 02 to 04 (phase 4 plan, section 9).

GET  /api/amaze/feeds/{id}                 the feed page (any backend role)
GET  /api/amaze/owners                     the users who hold data_owner (any backend role)
POST /api/amaze/feeds/{id}/profile         step 02 (backend_team)
POST /api/amaze/feeds/{id}/profile/summary the ProfilerAgent's summary of the latest profile (backend_team)
POST /api/amaze/feeds/{id}/signoff         step 03 (backend_team)
POST /api/amaze/feeds/{id}/register        step 04 (backend_team)

Named conflicts are 409, refused input 422, an unknown feed 404, a model that does not answer 503.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_api_consumer_sourcing import (  # noqa: F401 (client is a fixture)
    BLAKE,
    ONBOARDING,
    RILEY,
    VENDOR,
    client,
    find_source,
    page,
    post,
    raise_requirement,
    scoped_and_listed,
)

pytestmark = pytest.mark.anyio

STEWARD = {"X-Dev-User": "dev-data-steward"}


async def amaze(client, feed_id, action=None, body=None, headers=BLAKE, expect=200):
    path = f"/api/amaze/feeds/{feed_id}" + (f"/{action}" if action else "")
    response = await (client.post(path, headers=headers, json=body or {}) if action else client.get(path, headers=headers))
    assert response.status_code == expect, response.text
    return response.json()


async def feed_of(client, code):
    feeds = (await client.get("/api/amaze/feeds", headers=BLAKE)).json()
    return next(f for f in feeds if f["code"] == code)["id"]


async def onboarding_by_pick(client, query, element_keys, code):
    requirement_id, listed, _ = await scoped_and_listed(client, query, element_keys)
    source = listed[code]
    picked = await post(client, requirement_id, "pick", {"proposal_id": source["proposal_id"],
                                                         "accepted_conditions": source["source"]["conditions"]})
    assert (picked["fetch"]["outcome"], picked["message"]) == ("fetched", ONBOARDING)
    return requirement_id, await feed_of(client, code)


async def test_done_when_a_rejected_candidate_keeps_its_reasons_and_the_requester_is_told(client):
    """BUILD_PLAN.md phase 4 done-when, first half; row N of the plan."""
    requirement_id, feed_id = await onboarding_by_pick(client, "lot size", ["lot_size"], "syn_listing_details")

    profiled = await amaze(client, feed_id, "profile")
    assert profiled["result"]["outcome"] == "spec_only_low_confidence"
    assert profiled["result"]["detail"] == "The file has a header and no data rows."
    summary = await amaze(client, feed_id, "profile/summary")
    assert summary["summary"]["recommended_outcome"] == "meets_with_gaps"
    assert summary["feed"]["next_action"] == "sign_off"

    reasons = ["No data to check"]
    signed = await amaze(client, feed_id, "signoff", {"outcome": "rejected", "reasons": reasons})
    assert signed["result"]["outcome"] == "rejected"
    assert [(r["requirement_id"], r["reason"], r["status"]) for r in signed["result"]["released"]] == [
        (requirement_id, "candidate_rejected", "no_source_found")]

    feed = await amaze(client, feed_id)
    assert feed["feed"]["stage"] == "rejected"
    assert [(s["outcome"], s["reasons"], s["signed_off_by"]) for s in feed["signoffs"]] == [
        ("rejected", reasons, "dev-backend-team")]
    told = ("The backend team rejected Listing details: No data to check. No other source is listed. "
            "Add a note and ask for a new scope.")
    after = await page(client, requirement_id)
    assert (after["status"], after["message"]) == ("no_source_found", told)


async def test_done_when_an_accepted_candidate_is_registered(client):
    """BUILD_PLAN.md phase 4 done-when, second half; row L of the plan."""
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, requirement_id)
    feed_id = await feed_of(client, "syn_exchange_volumes")
    assert (await amaze(client, feed_id))["next_action"] == "profile"

    profiled = await amaze(client, feed_id, "profile")
    assert profiled["result"]["outcome"] == "profiled"
    assert [c["name"] for c in profiled["result"]["profile"]["columns"]] == ["instr_id", "trade_date", "traded_qty"]
    summary = await amaze(client, feed_id, "profile/summary")
    assert summary["summary"]["recommended_outcome"] == "meets"
    signed = await amaze(client, feed_id, "signoff", {"outcome": "meets"})
    assert (signed["result"]["outcome"], signed["feed"]["next_action"]) == ("meets", "register")

    no_owner = await amaze(client, feed_id, "register", {"visibility": "open"})
    assert (no_owner["result"]["outcome"], no_owner["result"]["reason"]) == ("no_owner", "none_named")
    registered = await amaze(client, feed_id, "register", {"owner_id": "dev-data-owner", "visibility": "open"})
    assert registered["result"]["outcome"] == "registered"
    registration = registered["result"]["registration"]
    assert (registration["owner_id"], registration["visibility"], registration["licence"]) == (
        "dev-data-owner", "open", "Synthetic exchange data licence")
    feeds = (await client.get("/api/amaze/feeds", params={"stage": "registered"}, headers=BLAKE)).json()
    assert [(f["code"], f["owner_id"]) for f in feeds] == [("syn_exchange_volumes", "dev-data-owner")]
    assert (await page(client, requirement_id))["message"] == ONBOARDING


async def test_register_queues_a_glossary_run_and_kicks_the_worker_after_its_commit(client):
    """Phase 5 plan, section 9: register also queues the run (step 05) and kicks the worker."""
    from apps.api.deps import get_glossary_kick
    from apps.api.main import app

    kicks = []
    app.dependency_overrides[get_glossary_kick] = lambda: (lambda: kicks.append("kick"))
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, requirement_id)
    feed_id = await feed_of(client, "syn_exchange_volumes")
    for action, body in (("profile", {}), ("profile/summary", {}), ("signoff", {"outcome": "meets"})):
        await amaze(client, feed_id, action, body)
    assert kicks == []
    registered = await amaze(client, feed_id, "register", {"owner_id": "dev-data-owner", "visibility": "open"})
    run_id = registered["result"]["glossary_run_id"]
    assert kicks == ["kick"] and run_id is not None
    run = registered["feed"]["glossary_run"]
    assert (run["id"], run["status"], run["reason"]) == (run_id, "queued", "registered")


async def test_register_with_a_stub_scheduler_is_200_and_the_run_is_taken_later(db_session, migrated_engine,
                                                                                tmp_path, monkeypatch):
    """Phase 6 plan, assumption 30 (review finding 7 before it said 503 or 200): with
    SCHEDULER_PROVIDER=azure_functions (a stub until phase 8) the kick after register's commit reaches the
    stub and fails. The committed write stands: the answer is 200 (the failure is logged, not raised), and the
    queued run is taken by the next worker pass."""
    from contextlib import nullcontext

    from agents.glossary_builder import GlossaryBuilderAgent
    from apps.api import main
    from apps.api.deps import get_blob_store_port
    from apps.api.jobs import GlossaryRuns
    from core.repositories.glossary_runs import GlossaryRunRepository
    from core.seed import seed_demo, seed_glossary
    from ports.config import Settings
    from ports.llm.mock import MockProvider
    from ports.scheduler.azure_functions import AzureFunctionsScheduler
    from ports.storage.local import LocalBlobStore
    from tests.conftest import running_app

    monkeypatch.setattr(main, "get_scheduler", lambda settings: AzureFunctionsScheduler())
    seed_glossary(db_session)
    seed_demo(db_session)
    db_session.commit()
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None), session=db_session,
                           glossary_runs=False) as stub_client:  # False: the app builds its own worker
        main.app.dependency_overrides[get_blob_store_port] = lambda: LocalBlobStore(tmp_path / "blob")
        requirement_id = await raise_requirement(stub_client, "volume", ["volume"])
        await find_source(stub_client, requirement_id)
        feed_id = await feed_of(stub_client, "syn_exchange_volumes")
        for action, body in (("profile", {}), ("profile/summary", {}), ("signoff", {"outcome": "meets"})):
            await amaze(stub_client, feed_id, action, body)
        response = await stub_client.post(f"/api/amaze/feeds/{feed_id}/register", headers=BLAKE,
                                          json={"owner_id": "dev-data-owner", "visibility": "open"})
        assert response.status_code == 200, response.text
        page_now = await amaze(stub_client, feed_id)
    assert page_now["feed"]["stage"] == "registered"
    run = GlossaryRunRepository(db_session).active(uuid.UUID(feed_id))
    assert (run.status, run.reason) == ("queued", "registered")  # committed, and nothing took it yet
    drained = GlossaryRuns(session_factory=lambda: nullcontext(db_session),
                           agent_factory=lambda: GlossaryBuilderAgent(provider=MockProvider(),
                                                                      blob_store=LocalBlobStore(tmp_path / "blob")),
                           owners=lambda: ["dev-glossary-owner"]).drain()  # the "later": a pass of the worker
    assert drained == {run.id: "done"}
    assert GlossaryRunRepository(db_session).latest(uuid.UUID(feed_id)).status == "done"


async def test_the_feed_page_shows_the_conditions_each_requester_accepted(client):
    requirement_id, feed_id = await onboarding_by_pick(client, "open price", ["open_price"], VENDOR)
    feed = await amaze(client, feed_id, headers=STEWARD)  # any backend role reads
    [requirement] = feed["requirements"]
    assert (requirement["id"], requirement["status"], requirement["basis"]) == (requirement_id, "onboarding",
                                                                                "requester_pick")
    assert requirement["accepted_conditions"]["external_approvals"] == [
        "A signed data agreement with the vendor, held by your desk"]
    assert feed["source"]["code"] == VENDOR and feed["last_file"]["outcome"] == "fetched"


async def test_the_owners_are_the_data_owners(client):
    owners = (await client.get("/api/amaze/owners", headers=STEWARD)).json()
    assert [(o["id"], o["display_name"]) for o in owners] == [("dev-data-owner", "Devon Owner")]


async def test_a_model_that_does_not_answer_gives_503_and_keeps_the_profile(client, db_session):
    from apps.api.deps import get_llm_provider_port
    from apps.api.main import app
    from core.repositories.feed_profiles import FeedProfileRepository
    from ports.llm.base import LLMUnavailable

    class Down:
        name = "down"

        def complete(self, **kwargs):
            raise LLMUnavailable("timeout")

    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, requirement_id)
    feed_id = await feed_of(client, "syn_exchange_volumes")
    await amaze(client, feed_id, "profile")
    app.dependency_overrides[get_llm_provider_port] = lambda: Down()
    response = await client.post(f"/api/amaze/feeds/{feed_id}/profile/summary", headers=BLAKE, json={})
    assert (response.status_code, response.json()["reason"]) == (503, "timeout")
    assert FeedProfileRepository(db_session).latest(uuid.UUID(feed_id)) is not None
    assert (await amaze(client, feed_id))["next_action"] == "summarise"


async def test_a_wrong_stage_or_step_is_409(client):
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, requirement_id)
    feed_id = await feed_of(client, "syn_exchange_volumes")
    await amaze(client, feed_id, "profile/summary", expect=409)  # FeedNotProfiled
    await amaze(client, feed_id, "signoff", {"outcome": "meets"}, expect=409)  # FeedNotProfiled
    await amaze(client, feed_id, "register", {"owner_id": "dev-data-owner", "visibility": "open"},
                expect=409)  # FeedNotSignedOff
    await amaze(client, feed_id, "profile")
    await amaze(client, feed_id, "signoff", {"outcome": "meets"}, expect=409)  # ProfileNotSummarised
    await amaze(client, feed_id, "profile/summary")
    await amaze(client, feed_id, "profile/summary", expect=409)  # ProfileAlreadySummarised
    await amaze(client, feed_id, "signoff", {"outcome": "meets"})
    await amaze(client, feed_id, "signoff", {"outcome": "meets"}, expect=409)  # ProposalAlreadyDecided
    await amaze(client, feed_id, "register", {"owner_id": "dev-data-owner", "visibility": "open"})
    await amaze(client, feed_id, "profile", expect=409)  # FeedNotACandidate
    await amaze(client, feed_id, "register", {"owner_id": "dev-data-owner", "visibility": "open"},
                expect=409)  # FeedNotACandidate


async def test_a_feed_with_no_file_is_409_and_an_unknown_feed_404(client):
    requirement_id = await raise_requirement(client, "accrued interest", ["accrued_interest"])
    await find_source(client, requirement_id)  # its file is missing on purpose: source_down
    await amaze(client, await feed_of(client, "syn_offline_accruals"), "profile", expect=409)  # FeedHasNoFile
    await amaze(client, uuid.uuid4(), expect=404)
    await amaze(client, uuid.uuid4(), "profile", expect=404)


@pytest.mark.parametrize("body", [{"outcome": "meets", "gaps": ["g"]}, {"outcome": "rejected"},
                                  {"outcome": "meets_with_gaps"}, {"outcome": "perhaps"}])
async def test_a_signoff_that_does_not_fit_is_422(client, body):
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, requirement_id)
    feed_id = await feed_of(client, "syn_exchange_volumes")
    await amaze(client, feed_id, "profile")
    await amaze(client, feed_id, "profile/summary")
    await amaze(client, feed_id, "signoff", body, expect=422)


async def test_only_the_backend_team_acts(client):
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, requirement_id)
    feed_id = await feed_of(client, "syn_exchange_volumes")
    for action, body in (("profile", {}), ("profile/summary", {}), ("signoff", {"outcome": "meets"}),
                         ("register", {"owner_id": "dev-data-owner", "visibility": "open"})):
        await amaze(client, feed_id, action, body, headers=STEWARD, expect=403)
        await amaze(client, feed_id, action, body, headers=RILEY, expect=403)
        await amaze(client, feed_id, action, body, headers={}, expect=401)
    await amaze(client, feed_id, headers=RILEY, expect=403)
