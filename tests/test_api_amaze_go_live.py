"""Amaze API for steps 09, 11 and 13 (phase 6 plan, sections 9 and 10).

POST /api/amaze/feeds/{id}/rules/propose      step 09 (data_steward)
POST /api/amaze/proposals/{id}/decision       gains rule (steward, then checker) and review_summary (the owner)
GET  /api/amaze/proposals?kind=rule|review_summary
POST /api/amaze/feeds/{id}/go-live/summary    step 11 prepare: the test load, then the summary (backend_team)
POST /api/amaze/feeds/{id}/loads              Load now (operations)
GET  /api/amaze/feeds/{id}                    gains rules, go-live and loads
GET  /api/amaze/loads/{id}                    one load, its stages and its first records

BUILD_PLAN.md phase 6 done-when: an approved feed loads end to end from configuration alone.
"""

from __future__ import annotations

import dataclasses
import uuid
from contextlib import nullcontext

import pytest

from apps.api.jobs import DailyFetch
from core.domain.profiling import FileColumn, SourceSpec
from core.repositories.feeds import FeedRepository
from ports.connectors.local_file import LocalFileConnector
from tests.onboarded import registered_feed
from tests.test_api_consumer_sourcing import BLAKE, RILEY, client  # noqa: F401 (client is a fixture)

pytestmark = pytest.mark.anyio

SAM = {"X-Dev-User": "dev-data-steward"}
CASEY = {"X-Dev-User": "dev-checker"}
DEVON = {"X-Dev-User": "dev-data-owner"}
OPAL = {"X-Dev-User": "dev-operations"}
CODE = "syn_exchange_volumes"
FILE = (b"instr_id,trade_date,traded_qty,note\nsyn-0001,2026-11-02,120500,a\nsyn-0002,2026-11-02,-5,b\n"
        b"syn-0003,2026-11-02,45010,c\nsyn-0004,2026-11-02,302000,d\n")
COLUMNS = [FileColumn(name=n, type=t, null_count=0, null_rate=0.0, distinct_count=4)
           for n, t in (("instr_id", "VARCHAR"), ("trade_date", "DATE"), ("traded_qty", "BIGINT"),
                        ("note", "VARCHAR"))]
SPEC = SourceSpec(source_code=CODE, elements=["instrument_id", "price_date", "volume"], other_fields=[],
                  columns={"instr_id": "instrument_id", "trade_date": "price_date", "traded_qty": "volume"})


@pytest.fixture
def kicks(client):
    from apps.api.deps import get_glossary_kick
    from apps.api.main import app

    made = []
    app.dependency_overrides[get_glossary_kick] = lambda: (lambda: made.append("kick"))
    return made


@pytest.fixture
def exchange(client, db_session, kicks, tmp_path):
    """syn_exchange_volumes, registered with a profile of four columns (`note` is declared by nothing), and a
    DailyFetch on the test's session reading a copy of the sample file that has a negative volume."""
    from apps.api.deps import get_daily_fetch
    from apps.api.main import app

    feed = FeedRepository(db_session).create_candidate(code=CODE, name="Exchange daily volumes", source_code=CODE,
                                                       connector="local_file", source_ref="files/exchange_volumes.csv")
    registered_feed(db_session, feed.id, spec=SPEC, columns=COLUMNS)
    db_session.commit()
    sources = tmp_path / "sources"
    (sources / "files").mkdir(parents=True)
    (sources / "files" / "exchange_volumes.csv").write_bytes(FILE)
    job = DailyFetch(session_factory=lambda: nullcontext(db_session), blob_store=client.blobs,
                     connector_factory=lambda name: LocalFileConnector(sources),
                     backend_team=lambda: ["dev-backend-team"], operations=lambda: ["dev-operations"])
    app.dependency_overrides[get_daily_fetch] = lambda: job
    return type("Exchange", (), {"id": str(feed.id), "job": job, "sources": sources})


async def call(client, method, path, body=None, headers=BLAKE, expect=200, **params):
    response = await client.request(method, f"/api/amaze/{path}", headers=headers, params=params or None,
                                    json=body if method == "POST" else None)
    assert response.status_code == expect, response.text
    return response.json()


async def decide(client, proposal_id, headers, decision="approved", note=None, back_to_step=None, expect=200):
    return await call(client, "POST", f"proposals/{proposal_id}/decision", headers=headers, expect=expect,
                      body={"decision": decision, "note": note, "back_to_step": back_to_step})


async def map_the_columns(client, feed_id):
    proposed = await call(client, "POST", f"feeds/{feed_id}/mappings/propose", {}, headers=SAM)
    for item in proposed["result"]["recommended"]:
        await decide(client, item["proposal_id"], SAM)
    return proposed


async def approve_rules(client, feed_id, keys=("volume_not_negative", "instrument_id_present")):
    proposed = await call(client, "POST", f"feeds/{feed_id}/rules/propose", {}, headers=SAM)
    pending = await call(client, "GET", "proposals", headers=SAM, kind="rule", status="pending", feed_id=feed_id)
    for proposal in pending:
        if proposal["payload"]["rule_key"] in keys:
            await decide(client, proposal["id"], SAM)
            await decide(client, proposal["id"], CASEY)
        else:
            await decide(client, proposal["id"], SAM, decision="rejected", note="Not needed for this feed.")
    return proposed


async def test_done_when_an_approved_feed_loads_end_to_end_from_configuration_alone(client, exchange, db_session):
    """BUILD_PLAN.md phase 6 done-when; rows U to X of the plan. No person and no request after the go-live."""
    from core.repositories.loads import LoadRepository
    from core.repositories.standard_records import StandardRecordRepository

    assert exchange.job.run() == {CODE: "fetched"}  # the file is in the store; the feed is not live: no load
    mapped = await map_the_columns(client, exchange.id)
    assert sorted(r["source_field"] for r in mapped["result"]["recommended"]) == ["instr_id", "trade_date",
                                                                                 "traded_qty"]
    proposed = await approve_rules(client, exchange.id)
    assert proposed["result"]["outcome"] == "rules_proposed" and len(proposed["result"]["proposal_ids"]) == 4
    assert [r["rule_key"] for r in proposed["result"]["returned"]] == ["volume_in_whole_lots"]
    page = await call(client, "GET", f"feeds/{exchange.id}")
    assert sorted(r["rule_key"] for r in page["rules"]["current"]) == ["instrument_id_present", "volume_not_negative"]

    prepared = await call(client, "POST", f"feeds/{exchange.id}/go-live/summary", {})
    summary = prepared["result"]
    assert summary["test_load"]["status"] == "loaded" and "note" in prepared["proposal"]["payload"]["summary"]
    approved = await decide(client, summary["proposal_id"], DEVON)
    assert approved["result"]["outcome"] == "approved_scheduled" and approved["result"]["daily_run_utc"] == "06:00"

    assert exchange.job.run() == {CODE: "fetched"}  # no request, no person: the daily job loads it
    page = await call(client, "GET", f"feeds/{exchange.id}")
    assert page["go_live"]["latest"]["version"] == 1 and page["go_live"]["pending_summary"] is None
    scheduled = next(load for load in page["loads"] if load["purpose"] == "scheduled")
    # was "loaded"; from phase 7 step 15 certifies it: 1 of 4 rows flagged scores 75.0, below the threshold of 90
    assert scheduled["status"] == "held"
    assert [(s["stage"], s["outcome"]) for s in scheduled["stages"]] == [
        (name, "stage_completed") for name in ("raw", "curated", "validated", "standard")]
    detail = await call(client, "GET", f"loads/{scheduled['id']}", headers=OPAL)
    assert len(detail["records"]) == 4 and all("note" not in r["values"] for r in detail["records"])  # unmapped: absent
    assert {r["row_number"]: r["flags"] for r in detail["records"]} == {
        1: [], 2: ["volume_not_negative"], 3: [], 4: []}  # the broken rule flags the negative volume
    assert StandardRecordRepository(db_session).count(uuid.UUID(scheduled["id"])) == 4
    assert LoadRepository(db_session).get(uuid.UUID(scheduled["id"])).status == "held"  # was "loaded" (see above)


async def test_the_roles_of_the_new_endpoints(client, exchange):
    await map_the_columns(client, exchange.id)
    for headers, path, body in ((RILEY, f"feeds/{exchange.id}/rules/propose", {}),
                                (BLAKE, f"feeds/{exchange.id}/rules/propose", {}),
                                (SAM, f"feeds/{exchange.id}/go-live/summary", {}),
                                (SAM, f"feeds/{exchange.id}/loads", {}),
                                (BLAKE, f"feeds/{exchange.id}/loads", {})):
        await call(client, "POST", path, body, headers=headers, expect=403)
    await call(client, "GET", f"feeds/{exchange.id}", headers=RILEY, expect=403)
    await call(client, "GET", f"loads/{uuid.uuid4()}", headers=RILEY, expect=403)
    await call(client, "GET", f"loads/{uuid.uuid4()}", headers=SAM, expect=404)


async def test_a_feed_with_no_mapping_has_no_rules_to_propose(client, exchange):
    await call(client, "POST", f"feeds/{exchange.id}/rules/propose", {}, headers=SAM, expect=409)
    await call(client, "POST", f"feeds/{exchange.id}/go-live/summary", {}, expect=409)  # NothingToGoLive


async def test_the_steps_of_a_rule_decision_and_a_rejection_that_needs_a_note(client, exchange):
    await map_the_columns(client, exchange.id)
    await call(client, "POST", f"feeds/{exchange.id}/rules/propose", {}, headers=SAM)
    [first, *_] = await call(client, "GET", "proposals", headers=SAM, kind="rule", feed_id=exchange.id)
    await decide(client, first["id"], CASEY, expect=403)  # step 1 is the steward's
    await decide(client, first["id"], SAM, decision="rejected", note=" ", expect=422)
    step_one = await decide(client, first["id"], SAM)
    assert step_one["result"] == {"proposal_id": first["id"], "rule_key": first["payload"]["rule_key"]}
    await decide(client, first["id"], SAM, expect=403)  # step 2 is the checker's
    stored = await decide(client, first["id"], CASEY)
    assert stored["result"]["rule"]["version"] == 1 and stored["proposal"]["status"] == "approved"
    await decide(client, first["id"], CASEY, expect=409)


async def test_a_go_live_summary_waits_for_its_owner_and_is_proposed_once(client, exchange):
    exchange.job.run()
    await map_the_columns(client, exchange.id)
    first = await call(client, "POST", f"feeds/{exchange.id}/go-live/summary", {})
    await call(client, "POST", f"feeds/{exchange.id}/go-live/summary", {}, expect=409)  # GoLiveAlreadyProposed
    page = await call(client, "GET", f"feeds/{exchange.id}")
    assert page["go_live"]["pending_summary"]["id"] == first["result"]["proposal_id"]
    await decide(client, first["result"]["proposal_id"], SAM, expect=403)  # not the owner's role


async def test_a_rejected_go_live_goes_back_to_a_step_and_needs_both_a_note_and_the_step(client, exchange):
    exchange.job.run()
    await map_the_columns(client, exchange.id)
    summary = (await call(client, "POST", f"feeds/{exchange.id}/go-live/summary", {}))["result"]["proposal_id"]
    await decide(client, summary, DEVON, decision="rejected", note="Map note.", expect=422)  # no step
    await decide(client, summary, DEVON, decision="rejected", note=" ", back_to_step="06", expect=422)
    rejected = await decide(client, summary, DEVON, decision="rejected", note="Map note.", back_to_step="06")
    assert rejected["result"] == {"outcome": "rejected_back_to_step", "back_to_step": "06", "note": "Map note."}
    page = await call(client, "GET", f"feeds/{exchange.id}")
    assert page["go_live"]["latest"] is None and page["go_live"]["pending_summary"] is None


async def test_a_configuration_changed_after_the_summary_cannot_be_approved(client, exchange, db_session):
    from tests.configured import approved_rule

    exchange.job.run()
    await map_the_columns(client, exchange.id)
    summary = (await call(client, "POST", f"feeds/{exchange.id}/go-live/summary", {}))["result"]["proposal_id"]
    approved_rule(db_session, uuid.UUID(exchange.id), "late_rule", sql="SELECT _row FROM curated", description="Late.")
    db_session.commit()
    await decide(client, summary, DEVON, expect=409)  # ConfigurationChangedSinceSummary


async def test_a_failed_test_load_is_test_failed_held_with_no_summary(client, exchange):
    exchange.job.run()
    await map_the_columns(client, exchange.id)
    (exchange.sources / "files" / "exchange_volumes.csv").write_bytes(
        b"instr_id,trade_date,traded_qty\nsyn-1,2026-11-02,12x\n")
    exchange.job.run()  # the bad file is now the latest fetched one
    held = (await call(client, "POST", f"feeds/{exchange.id}/go-live/summary", {}))["result"]
    assert (held["outcome"], held["stage"], held["reason"]) == ("test_failed_held", "curated", "values_do_not_cast")
    page = await call(client, "GET", f"feeds/{exchange.id}")
    assert page["go_live"]["pending_summary"] is None
    assert [(load["purpose"], load["status"], load["failed_stage"]) for load in page["loads"]
            if load["status"] != "loaded"] == [("go_live_test", "failed_rolled_back", "curated")]


async def test_load_now_needs_a_go_live_then_fetches_and_loads(client, exchange):
    await call(client, "POST", f"feeds/{exchange.id}/loads", {}, headers=OPAL, expect=409)  # NotLive
    exchange.job.run()
    await map_the_columns(client, exchange.id)
    summary = (await call(client, "POST", f"feeds/{exchange.id}/go-live/summary", {}))["result"]["proposal_id"]
    await decide(client, summary, DEVON)
    loaded = await call(client, "POST", f"feeds/{exchange.id}/loads", {}, headers=OPAL)
    assert loaded["result"] == {"fetch": "fetched", "load": "released"}  # was "loaded" before phase 7
    assert loaded["feed"]["loads"][0]["purpose"] == "scheduled"


async def test_the_proposals_list_gains_rules_and_summaries(client, exchange):
    exchange.job.run()
    await map_the_columns(client, exchange.id)
    await call(client, "POST", f"feeds/{exchange.id}/rules/propose", {}, headers=SAM)
    await call(client, "POST", f"feeds/{exchange.id}/go-live/summary", {})
    rules = await call(client, "GET", "proposals", headers=CASEY, kind="rule")
    summaries = await call(client, "GET", "proposals", headers=CASEY, kind="review_summary")
    assert len(rules) == 4 and all(r["kind"] == "rule" and r["required_approver_roles"] == ["data_steward", "checker"]
                                   for r in rules)
    assert [s["kind"] for s in summaries] == ["review_summary"] and summaries[0]["required_approver_roles"] == [
        "data_owner"]
    assert "backtest" in rules[0]["payload"]
    assert dataclasses.is_dataclass(exchange.job)
