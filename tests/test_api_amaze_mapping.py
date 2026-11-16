"""Amaze API for steps 05 and 06 (phase 5 plan, section 9).

GET  /api/amaze/glossary/runs                 the banner (any backend role)
POST /api/amaze/feeds/{id}/glossary/run       queue a run, or the active one; then kick (glossary_owner, backend_team)
GET  /api/amaze/proposals?kind=&status=&feed_id=   glossary_element and mapping proposals (any backend role)
POST /api/amaze/feeds/{id}/mappings/propose   step 06 for the unmapped columns, or the one named (data_steward)
POST /api/amaze/proposals/{id}/decision       step 05's or 06's decision, by the proposal's kind (its approver)
GET  /api/amaze/feeds/{id}                    gains the glossary run and the mapping table

The kick is a dependency: these tests record it, and run the worker themselves on the test's session.
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext

import pytest

from core.repositories.feeds import FeedRepository
from tests.onboarded import registered_feed
from tests.test_api_consumer_sourcing import BLAKE, RILEY, client  # noqa: F401 (client is a fixture)
from tests.test_s05_glossary import BOND_COLUMNS, BOND_SPEC

pytestmark = pytest.mark.anyio

GLENN = {"X-Dev-User": "dev-glossary-owner"}
SAM = {"X-Dev-User": "dev-data-steward"}


@pytest.fixture
def kicks(client):
    from apps.api.deps import get_glossary_kick
    from apps.api.main import app

    made = []
    app.dependency_overrides[get_glossary_kick] = lambda: (lambda: made.append("kick"))
    return made


@pytest.fixture
def bond(client, db_session):
    feed = FeedRepository(db_session).create_candidate(code="syn_bond_terms", name="Bond terms")
    feed = registered_feed(db_session, feed.id, spec=BOND_SPEC, columns=BOND_COLUMNS)
    db_session.commit()
    return str(feed.id)


@pytest.fixture
def drain(db_session, tmp_path):
    """The step 05 worker, run by the test on its own session (the API under test runs none)."""
    from agents.glossary_builder import GlossaryBuilderAgent
    from apps.api.jobs import GlossaryRuns
    from ports.llm.mock import MockProvider
    from ports.storage.local import LocalBlobStore

    def _drain():
        return GlossaryRuns(session_factory=lambda: nullcontext(db_session),
                            agent_factory=lambda: GlossaryBuilderAgent(provider=MockProvider(),
                                                                       blob_store=LocalBlobStore(tmp_path / "b")),
                            owners=lambda: ["dev-glossary-owner"]).drain()

    return _drain


async def call(client, method, path, body=None, headers=SAM, expect=200, **params):
    response = await client.request(method, f"/api/amaze/{path}", headers=headers, params=params or None,
                                    json=body if method == "POST" else None)
    assert response.status_code == expect, response.text
    return response.json()


async def propose(client, feed_id, column=None, **kw):
    return await call(client, "POST", f"feeds/{feed_id}/mappings/propose", {"column": column} if column else {}, **kw)


async def decide(client, proposal_id, decision="approved", choice=None, note=None, **kw):
    return await call(client, "POST", f"proposals/{proposal_id}/decision",
                      {"decision": decision, "choice": choice, "note": note}, **kw)


def column(page, name):
    return next(c for c in page["columns"] if c["name"] == name)


async def test_done_when_approved_mappings_are_stored_as_versioned_configuration(client, bond, kicks, db_session):
    """BUILD_PLAN.md phase 5 done-when; rows R and S of the plan."""
    from core.repositories.configuration import MappingRepository

    proposed = await propose(client, bond)
    recommended = {r["source_field"]: r for r in proposed["result"]["recommended"]}
    assert set(recommended) == {"instr_id", "matures_on", "cpn", "venue_mic_code"}
    tie = next(r for r in proposed["result"]["results"] if r["outcome"] == "tie_to_steward")
    assert (tie["source_field"], tie["candidates"]) == ("date", ["issue_date", "maturity_date", "price_date"])

    approved = await decide(client, recommended["matures_on"]["proposal_id"])
    assert approved["result"]["outcome"] == "mapped"
    first = await decide(client, tie["proposal_id"], choice="price_date")
    assert (first["result"]["mapping"]["element_key"], first["result"]["mapping"]["version"]) == ("price_date", 1)

    again = await propose(client, bond, "date")
    [tie2] = again["result"]["results"]
    assert tie2["outcome"] == "tie_to_steward"
    await decide(client, tie2["proposal_id"], choice="maturity_date", expect=409)  # matures_on holds it
    second = await decide(client, tie2["proposal_id"], choice="issue_date")
    assert (second["result"]["mapping"]["element_key"], second["result"]["mapping"]["version"]) == ("issue_date", 2)

    history = MappingRepository(db_session).history(uuid.UUID(bond), "date")
    assert [(m.element_key, m.version) for m in history] == [("price_date", 1), ("issue_date", 2)]
    date = column(await call(client, "GET", f"feeds/{bond}"), "date")
    assert (date["mapped_to"], date["mapped_version"]) == ("issue_date", 2)
    assert [(v["element_key"], v["version"]) for v in date["versions"]] == [("price_date", 1), ("issue_date", 2)]


async def test_a_glossary_run_is_queued_once_kicked_and_shown_in_the_banner(client, bond, kicks, drain):
    queued = await call(client, "POST", f"feeds/{bond}/glossary/run", headers=GLENN)
    assert (queued["created"], queued["run"]["status"], queued["run"]["reason"]) == (True, "queued", "requested")
    again = await call(client, "POST", f"feeds/{bond}/glossary/run", headers=BLAKE)
    assert (again["created"], again["run"]["id"]) == (False, queued["run"]["id"])
    assert kicks == ["kick", "kick"]
    banner = await call(client, "GET", "glossary/runs")
    assert [(r["id"], r["feed_code"], r["status"]) for r in banner["active"]] == [
        (queued["run"]["id"], "syn_bond_terms", "queued")]

    drain()
    banner = await call(client, "GET", "glossary/runs")
    assert banner["active"] == [] and banner["waiting_proposals"] == 2
    [done] = banner["finished"]
    assert (done["id"], done["status"], done["counts"]) == (
        queued["run"]["id"], "done", {"proposed": 1, "duplicate_alias": 1})
    assert (await call(client, "GET", f"feeds/{bond}"))["glossary_run"]["id"] == queued["run"]["id"]


async def test_the_glossary_owner_decides_what_the_builder_proposed(client, bond, kicks, drain):
    await call(client, "POST", f"feeds/{bond}/glossary/run", headers=GLENN)
    drain()
    waiting = await call(client, "GET", "proposals", kind="glossary_element", status="pending", feed_id=bond)
    by_column = {p["column"]: p for p in waiting}
    assert set(by_column) == {"rating", "venue_mic_code"}
    rating = by_column["rating"]
    assert (rating["feed_code"], rating["payload"]["outcome"], rating["status"]) == ("syn_bond_terms", "proposed",
                                                                                      "pending")
    assert rating["reason"] and rating["decisions"] == []

    element = await decide(client, rating["id"], headers=GLENN)
    assert (element["result"]["decision"], element["result"]["element"]["version"]) == ("approved", 1)
    alias = await decide(client, by_column["venue_mic_code"]["id"], headers=GLENN)
    assert (alias["result"]["element"]["key"], "venue_mic_code" in alias["result"]["element"]["aliases"]) == (
        "exchange_mic", True)
    decided = await call(client, "GET", "proposals", kind="glossary_element", feed_id=bond)
    assert {p["status"] for p in decided} == {"approved"}
    assert [d["decided_by"] for d in decided[0]["decisions"]] == ["dev-glossary-owner"]

    proposed = await propose(client, bond)  # rating now has an element: recommended
    assert "rating" in {r["source_field"] for r in proposed["result"]["recommended"]}


async def test_the_feed_page_says_why_a_column_has_no_mapping(client, bond, kicks, drain):
    page = await call(client, "GET", f"feeds/{bond}")
    assert column(page, "maturity")["why_none"] == "type_does_not_fit"
    assert (column(page, "cpn")["source_says"], column(page, "cpn")["why_none"]) == ("coupon_rate", None)

    proposed = await propose(client, bond)
    back = {r["source_field"]: r for r in proposed["result"]["results"] if r["outcome"] == "no_element_back_to_05"}
    assert set(back) == {"rating", "maturity"} and back["rating"]["glossary_run_id"] is not None
    assert kicks == ["kick"]  # the sent-back run is kicked after the commit
    page = await call(client, "GET", f"feeds/{bond}")
    assert column(page, "maturity")["why_none"] == "with_glossary_builder"
    assert column(page, "cpn")["pending"]["recommended"] == "coupon_rate"
    assert column(page, "date")["pending"]["candidates"] == ["issue_date", "maturity_date", "price_date"]

    drain()
    conflict = next(p for p in await call(client, "GET", "proposals", kind="glossary_element", status="pending")
                    if p["column"] == "maturity")
    assert conflict["payload"]["outcome"] == "conflict_to_owner"
    await decide(client, conflict["id"], "rejected", note="A tenor, not a date", headers=GLENN)
    assert column(await call(client, "GET", f"feeds/{bond}"), "maturity")["why_none"] == "rejected_by_glossary_owner"


async def test_a_model_that_does_not_answer_gives_503_and_writes_nothing(client, bond, kicks, db_session):
    from sqlalchemy import func, select

    from apps.api.deps import get_llm_provider_port
    from apps.api.main import app
    from core.repositories.tables import ProposalLogEntry, ProposalRow
    from ports.llm.base import LLMUnavailable
    from tests.stand_in_agents import FixedProvider

    count = lambda table: db_session.scalar(select(func.count()).select_from(table))  # noqa: E731
    before = (count(ProposalLogEntry), count(ProposalRow))
    app.dependency_overrides[get_llm_provider_port] = lambda: FixedProvider(error=LLMUnavailable("timeout"))
    response = await client.post(f"/api/amaze/feeds/{bond}/mappings/propose", headers=SAM, json={})
    assert (response.status_code, response.json()["reason"]) == (503, "timeout")
    assert (count(ProposalLogEntry), count(ProposalRow)) == before and kicks == []


async def test_a_wrong_state_is_409(client, bond, kicks, db_session):
    candidate = str(FeedRepository(db_session).create_candidate(code="syn_not_yet", name="Not yet").id)
    db_session.commit()
    await propose(client, candidate, expect=409)  # FeedNotRegistered
    await call(client, "POST", f"feeds/{candidate}/glossary/run", headers=GLENN, expect=409)  # FeedNotRegistered
    await propose(client, bond, "no_such_column", expect=409)  # NothingToMap
    proposed = await propose(client, bond)
    cpn = next(r for r in proposed["result"]["recommended"] if r["source_field"] == "cpn")
    await propose(client, bond, "cpn", expect=409)  # MappingAlreadyProposed
    await decide(client, cpn["proposal_id"])
    await decide(client, cpn["proposal_id"], expect=409)  # ProposalAlreadyDecided
    await decide(client, cpn["proposal_id"], "rejected", note="late", expect=409)
    assert kicks == ["kick"]  # only the sent-back run of the one propose that committed


async def test_only_the_named_roles_act(client, bond, kicks):
    for headers in (SAM, RILEY):
        await call(client, "POST", f"feeds/{bond}/glossary/run", headers=headers, expect=403)
    await call(client, "POST", f"feeds/{bond}/glossary/run", headers={}, expect=401)
    for headers in (GLENN, BLAKE, RILEY):
        await propose(client, bond, headers=headers, expect=403)
    proposed = await propose(client, bond)
    cpn = next(r for r in proposed["result"]["recommended"] if r["source_field"] == "cpn")
    for headers in (GLENN, BLAKE, RILEY):
        await decide(client, cpn["proposal_id"], headers=headers, expect=403)
    for path in ("glossary/runs", "proposals", f"feeds/{bond}"):
        await call(client, "GET", path, headers=RILEY, expect=403)
        await call(client, "GET", path, headers=BLAKE)


async def test_a_glossary_proposal_is_decided_by_the_glossary_owner_only(client, bond, kicks, drain):
    await call(client, "POST", f"feeds/{bond}/glossary/run", headers=BLAKE)
    drain()
    [first, *_] = await call(client, "GET", "proposals", kind="glossary_element", status="pending")
    for headers in (SAM, BLAKE):
        await decide(client, first["id"], headers=headers, expect=403)
        await decide(client, first["id"], "rejected", note="no", headers=headers, expect=403)


async def test_a_backend_user_without_the_approver_role_gets_403_and_nothing_is_decided(client, bond, kicks, drain,
                                                                                      db_session):
    """Both kinds: the role is the proposal's, checked by the step (NotAnApprover -> 403, never 500)."""
    from core.repositories.proposals import ProposalRepository

    proposed = await propose(client, bond)
    mapping_id = next(r for r in proposed["result"]["recommended"] if r["source_field"] == "cpn")["proposal_id"]
    await call(client, "POST", f"feeds/{bond}/glossary/run", headers=GLENN)
    drain()
    glossary_id = next(p for p in await call(client, "GET", "proposals", kind="glossary_element", status="pending")
                       if p["column"] == "rating")["id"]
    for proposal_id, others in ((glossary_id, (SAM, BLAKE)), (mapping_id, (GLENN, BLAKE))):
        for headers in others:
            for decision in ("approved", "rejected"):
                response = await client.post(f"/api/amaze/proposals/{proposal_id}/decision", headers=headers,
                                             json={"decision": decision, "note": "not mine"})
                assert response.status_code == 403, response.text
                assert "needs role" in response.json()["detail"]
        db_session.expire_all()
        proposal = ProposalRepository(db_session).get(uuid.UUID(proposal_id))
        assert proposal.status == "pending" and ProposalRepository(db_session).approvals(proposal.id) == []


async def test_refused_input_is_422(client, bond, kicks, drain):
    proposed = await propose(client, bond)
    tie = next(r for r in proposed["result"]["results"] if r["outcome"] == "tie_to_steward")
    await decide(client, tie["proposal_id"], expect=422)  # ChoiceNeeded
    await decide(client, tie["proposal_id"], choice="isin", expect=422)  # ChoiceNotOffered
    await decide(client, tie["proposal_id"], choice=1, expect=422)  # a mapping choice is an element key
    await decide(client, tie["proposal_id"], "perhaps", expect=422)
    await decide(client, tie["proposal_id"], "rejected", note="x" * 501, expect=422)
    await call(client, "POST", f"proposals/{tie['proposal_id']}/decision", {"decision": "approved", "extra": 1},
               expect=422)
    await call(client, "POST", f"feeds/{bond}/mappings/propose", {"column": "cpn", "extra": 1}, expect=422)
    await call(client, "GET", "proposals", kind="profile_summary", expect=422)
    await call(client, "POST", f"feeds/{bond}/glossary/run", headers=GLENN)
    drain()
    [glossary] = [p for p in await call(client, "GET", "proposals", kind="glossary_element", status="pending")
                  if p["column"] == "rating"]
    await decide(client, glossary["id"], choice="first", headers=GLENN, expect=422)  # an option is an index
    await decide(client, glossary["id"], choice=5, headers=GLENN, expect=422)  # ChoiceNotOffered


async def test_an_unknown_feed_or_proposal_is_404(client, kicks):
    await propose(client, uuid.uuid4(), expect=404)
    await call(client, "POST", f"feeds/{uuid.uuid4()}/glossary/run", headers=GLENN, expect=404)
    await decide(client, uuid.uuid4(), expect=404)
