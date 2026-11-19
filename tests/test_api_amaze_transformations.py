"""Amaze API for step 10 (phase 7.8, task 4.3, assumptions 27 to 29).

POST /api/amaze/feeds/{id}/transformations/propose   the TransformationAgent for the mapped columns (data_steward)
POST /api/amaze/proposals/{id}/decision              a transformation proposal's decision (data_steward)
PUT  /api/amaze/feeds/{id}/key-policy                the feed's duplicate-key policy (data_steward)
GET  /api/amaze/feeds/{id}                           gains the transformations and the effective key policy
"""

from __future__ import annotations

import pytest

from core.repositories.feeds import FeedRepository
from core.repositories.transformations import TransformationRepository
from tests.configured import a_registered_feed
from tests.test_agent_transformation import master_mapped
from tests.test_api_amaze_mapping import SAM, call, decide
from tests.test_api_consumer_sourcing import BLAKE, RILEY, client  # noqa: F401 (client is a fixture)

pytestmark = pytest.mark.anyio


@pytest.fixture
def master(client, db_session, tmp_path):
    feed_id = master_mapped(db_session, tmp_path)
    db_session.commit()
    return str(feed_id)


async def propose(client, feed_id, **kw):
    return await call(client, "POST", f"feeds/{feed_id}/transformations/propose", {}, **kw)


async def set_policy(client, feed_id, body, headers=SAM, expect=200):
    response = await client.put(f"/api/amaze/feeds/{feed_id}/key-policy", headers=headers, json=body)
    assert response.status_code == expect, response.text
    return response.json()


POLICY = {"policy": "keep_last", "key_columns": ["TICKER_NAME"], "reason": "The last row is the right one."}


async def test_propose_decide_and_the_feed_page_show_the_transformations(client, master, db_session):
    proposed = await propose(client, master)
    assert proposed["result"]["outcome"] == "transforms_proposed" and len(proposed["result"]["proposal_ids"]) == 4
    pending = proposed["feed"]["transformations"]["pending"]
    assert sorted(p["column"] for p in pending) == ["COUNTRY", "F_PERIOD", "RECORD_DATE", "REGION"]
    assert {p["kind"] for p in pending} == {"transformation"}
    assert proposed["feed"]["transformations"]["current"] == []

    date = next(p for p in pending if p["column"] == "RECORD_DATE")
    assert date["payload"]["steps"] == [{"op": "parse_date", "format": "DD-MM-YYYY"}]
    approved = await decide(client, date["id"])
    assert approved["result"]["transformation"]["source_field"] == "RECORD_DATE"
    assert (approved["result"]["transformation"]["version"], approved["proposal"]["status"]) == (1, "approved")
    country = next(p for p in pending if p["column"] == "COUNTRY")
    rejected = await decide(client, country["id"], "rejected", note="Keep the placeholder.")
    assert (rejected["result"]["source_field"], rejected["proposal"]["status"]) == ("COUNTRY", "rejected")

    page = await call(client, "GET", f"feeds/{master}")
    assert [t["source_field"] for t in page["transformations"]["current"]] == ["RECORD_DATE"]
    assert sorted(p["column"] for p in page["transformations"]["pending"]) == ["F_PERIOD", "REGION"]
    assert [t.source_field for t in TransformationRepository(db_session).current_for_feed(
        FeedRepository(db_session).get_by_code("syn_security_master").id)] == ["RECORD_DATE"]


async def test_a_second_propose_leaves_out_the_pending_columns_and_proposes_nothing_new(client, master):
    first = await propose(client, master)
    second = await propose(client, master)  # only the two clean columns are left, and the mock has nothing for them
    assert second["result"]["outcome"] == "nothing_to_transform"
    assert sorted(p["id"] for p in second["feed"]["transformations"]["pending"]) == sorted(
        first["result"]["proposal_ids"])


async def test_the_proposals_list_filters_by_the_transformation_kind(client, master):
    await propose(client, master)
    listed = await call(client, "GET", "proposals", kind="transformation", status="pending")
    assert len(listed) == 4 and {p["kind"] for p in listed} == {"transformation"}


async def test_only_a_data_steward_proposes_and_decides(client, master):
    await propose(client, master, headers=BLAKE, expect=403)
    await propose(client, master, headers=RILEY, expect=403)
    proposed = await propose(client, master)
    proposal_id = proposed["result"]["proposal_ids"][0]
    await decide(client, proposal_id, headers=BLAKE, expect=403)
    await decide(client, proposal_id, "rejected", expect=422)  # a rejection needs a note
    await decide(client, proposal_id)
    await decide(client, proposal_id, expect=409)  # already decided


async def test_propose_conflicts_and_not_found(client, db_session):
    candidate = FeedRepository(db_session).create_candidate(code="syn_candidate", name="Candidate")
    nothing_mapped = a_registered_feed(db_session)
    db_session.commit()
    await propose(client, candidate.id, expect=409)  # not registered
    await propose(client, nothing_mapped, expect=409)  # no mapped column
    await propose(client, "00000000-0000-0000-0000-000000000000", expect=404)


async def test_the_key_policy_defaults_to_flag_then_is_set_and_versioned(client, master):
    page = await call(client, "GET", f"feeds/{master}")
    default = page["transformations"]["key_policy"]
    assert (default["policy"], default["source"], default["key_columns"], default["version"]) == (
        "flag", "default", [], None)

    done = await set_policy(client, master, POLICY)
    assert done["key_policy"]["policy"] == "keep_last" and done["key_policy"]["source"] == "set"
    assert done["feed"]["transformations"]["key_policy"]["version"] == 1
    again = await set_policy(client, master, {**POLICY, "policy": "hold"})
    assert (again["key_policy"]["version"], again["key_policy"]["policy"], again["key_policy"]["set_by"]) == (
        2, "hold", "dev-data-steward")


async def test_the_key_policy_refusals(client, master, db_session):
    await set_policy(client, master, POLICY, headers=BLAKE, expect=403)
    await set_policy(client, master, {**POLICY, "key_columns": ["NO_SUCH"]}, expect=422)  # not in the profile
    await set_policy(client, master, {**POLICY, "key_columns": []}, expect=422)
    await set_policy(client, master, {**POLICY, "key_columns": ["a"] * 2}, expect=422)
    await set_policy(client, master, {**POLICY, "policy": "delete_all"}, expect=422)
    await set_policy(client, master, {**POLICY, "reason": " "}, expect=422)
    await set_policy(client, master, {**POLICY, "extra": 1}, expect=422)
    candidate = FeedRepository(db_session).create_candidate(code="syn_candidate", name="Candidate")
    db_session.commit()
    await set_policy(client, candidate.id, POLICY, expect=409)
    await set_policy(client, "00000000-0000-0000-0000-000000000000", POLICY, expect=404)
    page = await call(client, "GET", f"feeds/{master}")
    assert page["transformations"]["key_policy"]["source"] == "default"  # nothing was stored
