"""Consumer API (apps/api/routers/consumer.py), /api/consumer/, for the requester only (decision 10).

Each request runs on the test's rolled-back session. Test data is committed to that session's
savepoint first, because the endpoint commits or rolls back itself (phase 2 plan, risk 10).
"""

import pytest

from apps.api.deps import get_request_notifier
from core.repositories.consumer_requests import ConsumerRequestRepository
from core.repositories.notifications import NotificationRepository
from core.repositories.requirements import RequirementRepository
from core.seed import seed_demo, seed_glossary
from ports.config import Settings
from ports.notify.inapp import InAppNotifier
from tests.conftest import running_app

pytestmark = pytest.mark.anyio

RILEY = {"X-Dev-User": "dev-requester"}
RAISED = "Requirement raised; the backend team will look for a source."


@pytest.fixture
async def client(db_session, migrated_engine):
    seed_glossary(db_session)
    seed_demo(db_session)
    db_session.commit()
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None), session=db_session) as c:
        yield c


async def search(client, q):
    response = await client.get("/api/consumer/search", params={"q": q}, headers=RILEY)
    assert response.status_code == 200, response.text
    return response.json()


async def ask(client, query, element_keys=(), terms=(), purpose="", expect=201):
    response = await client.post("/api/consumer/requests", headers=RILEY, json={
        "query": query, "purpose": purpose, "element_keys": list(element_keys), "terms": list(terms),
    })
    assert response.status_code == expect, response.text
    return response.json()


def hits(found):
    return [(h["outcome"], h["product"]["code"]) for h in found["hits"]]


def ticked(found):
    """What the UI ticks first: elements that start ticked. Terms and candidates start unticked."""
    return [e["key"] for e in found["match"]["elements"] if e["ticked"]]


# ---- the nine searches of plan section 8


async def test_row_1_close_price_is_reuse(client):
    found = await search(client, "close price")
    assert hits(found) == [("found", "demo_daily_close_prices")]
    assert found["element_names"] == {"close_price": "Close price"}
    result = await ask(client, "close price", ticked(found))
    assert (result["routing"]["outcome"], result["message"]) == (
        "reuse", "This data is available in Daily close prices.",
    )


async def test_row_2_maturity_date_is_restricted_with_the_route(client):
    found = await search(client, "maturity date")
    assert hits(found) == [("not_entitled_visible", "demo_bond_reference")]
    assert found["hits"][0]["route"] == "Ask Devon Owner for access to Bond reference data"
    result = await ask(client, "maturity date", ticked(found))
    assert result["routing"]["outcome"] == "reuse"
    assert result["message"] == ("This data is in Bond reference data. Access restricted. "
                                 "Ask Devon Owner for access to Bond reference data.")


async def test_row_3_close_and_bid_price_is_compose(client):
    found = await search(client, "close price and bid price")
    assert hits(found) == [("found", "demo_bid_ask_quotes"), ("found", "demo_daily_close_prices")]
    result = await ask(client, "close price and bid price", ticked(found))
    assert result["routing"]["outcome"] == "compose"
    assert [p["code"] for p in result["routing"]["products"]] == ["demo_bid_ask_quotes", "demo_daily_close_prices"]


async def test_row_4_close_price_and_volume_raises_a_requirement_for_volume(client):
    found = await search(client, "close price and volume")
    assert hits(found) == [("found", "demo_daily_close_prices")]
    result = await ask(client, "close price and volume", ticked(found))
    assert (result["routing"]["outcome"], result["requirement"]["outcome"]) == ("raise_requirement", "defined")
    assert result["routing"]["elements"] == ["volume"]
    assert result["notes"] == ["Already available: Close price in Daily close prices."]


async def test_row_5_volume_has_no_hits_and_raises_a_requirement(client):
    found = await search(client, "volume")
    assert found["hits"] == []
    result = await ask(client, "volume", ticked(found))
    assert (result["routing"]["outcome"], result["requirement"]["outcome"], result["message"]) == (
        "raise_requirement", "defined", RAISED,
    )


async def test_row_6_esg_score_is_an_unticked_term(client):
    found = await search(client, "esg score")
    assert (found["hits"], ticked(found), found["match"]["terms"]) == ([], [], ["esg score"])
    await ask(client, "esg score", expect=422)  # nothing ticked
    result = await ask(client, "esg score", terms=["esg score"])
    assert (result["routing"]["outcome"], result["requirement"]["outcome"]) == (
        "raise_requirement", "element_not_in_glossary",
    )


async def test_row_7_close_price_and_esg_score(client):
    found = await search(client, "close price and esg score")
    assert hits(found) == [("found", "demo_daily_close_prices")]
    unticked = await ask(client, "close price and esg score", ticked(found))
    assert unticked["routing"]["outcome"] == "reuse"
    with_term = await ask(client, "close price and esg score", ticked(found), ["esg score"])
    assert with_term["routing"]["outcome"] == "raise_requirement"
    assert [p["code"] for p in with_term["routing"]["covered_by"]] == ["demo_daily_close_prices"]


async def test_row_8_pricing_is_a_domain_filter_with_nothing_to_tick(client):
    found = await search(client, "pricing")
    assert hits(found) == [("found", "demo_bid_ask_quotes"), ("found", "demo_daily_close_prices")]
    assert (ticked(found), found["match"]["terms"], found["match"]["domains"]) == ([], [], ["pricing"])
    await ask(client, "pricing", expect=422)


async def test_row_9_quotes_is_a_word_of_a_product_name(client):
    found = await search(client, "quotes")
    assert hits(found) == [("found", "demo_bid_ask_quotes")]
    assert (ticked(found), found["match"]["terms"]) == ([], ["quotes"])


# ---- the done-when of BUILD_PLAN.md phase 2


async def test_done_when_missing_data_raises_a_requirement_and_shows_its_status(client):
    found = await search(client, "volume")
    assert found["hits"] == []
    raised = await ask(client, "volume", ticked(found))
    assert (raised["routing"]["outcome"], raised["message"]) == ("raise_requirement", RAISED)

    mine = (await client.get("/api/consumer/requests", headers=RILEY)).json()
    assert len(mine) == 1
    assert (mine[0]["id"], mine[0]["query"], mine[0]["outcome"], mine[0]["requirement_status"], mine[0]["message"]) == (
        raised["request_id"], "volume", "raise_requirement", "defined", RAISED,
    )
    detail = await client.get(f"/api/consumer/requests/{raised['request_id']}", headers=RILEY)
    assert detail.status_code == 200
    assert detail.json()["result"]["message"] == RAISED


# ---- requests: listing, ownership, terms


async def test_my_requests_lists_every_outcome(client, db_session):
    from core.repositories.feeds import FeedRepository

    feeds = FeedRepository(db_session)
    from tests.onboarded import registered_feed

    feed = registered_feed(db_session, feeds.create_candidate(code="synthetic_feed", name="Synthetic").id)
    from tests.configured import a_load, approved_mapping

    approved_mapping(db_session, feed.id, "Vol", "volume")
    a_load(db_session, feed.id, "released")  # 0018: loaded first, then released
    db_session.commit()

    await ask(client, "close price", ["close_price"])
    await ask(client, "close and bid", ["close_price", "bid_price"])
    await ask(client, "volume", ["volume"])
    await ask(client, "esg score", terms=["esg score"])
    mine = (await client.get("/api/consumer/requests", headers=RILEY)).json()
    assert [(r["outcome"], r["requirement_status"]) for r in mine] == [
        ("raise_requirement", "defined"), ("create_product", None), ("compose", None), ("reuse", None),
    ]


async def test_only_ticked_terms_are_kept(client, db_session):
    found = await search(client, "esg score and carbon footprint")
    assert found["match"]["terms"] == ["esg score", "carbon footprint"]
    result = await ask(client, "esg score and carbon footprint", terms=["esg score"])
    requirement = RequirementRepository(db_session).get(result["requirement"]["requirement"]["id"])
    assert requirement.terms == ["esg score"]


async def test_another_requesters_request_is_404(client, db_session):
    other = ConsumerRequestRepository(db_session).record(
        requester_id="dev-someone-else", query="close price", element_keys=["close_price"], outcome="reuse",
        result={"message": "x"},
    )
    db_session.commit()
    response = await client.get(f"/api/consumer/requests/{other.id}", headers=RILEY)
    assert response.status_code == 404
    unknown = await client.get("/api/consumer/requests/00000000-0000-4000-8000-000000000000", headers=RILEY)
    assert (unknown.status_code, unknown.json()) == (404, response.json())
    assert (await client.get("/api/consumer/requests", headers=RILEY)).json() == []


async def test_blank_query_returns_empty(client):
    for q in ("", "   "):
        found = await search(client, q)
        assert found["hits"] == []
        assert found["match"] == {"elements": [], "ambiguous": [], "domains": [], "terms": []}


# ---- who may call


async def test_no_user_gets_401(client):
    for method, path in (("GET", "/api/consumer/search?q=x"), ("GET", "/api/consumer/requests"),
                         ("POST", "/api/consumer/requests")):
        assert (await client.request(method, path, json={})).status_code == 401, path


@pytest.mark.parametrize("user", ["dev-backend-team", "dev-data-owner", "dev-data-steward", "dev-checker",
                                  "dev-glossary-owner", "dev-operations"])
async def test_backend_user_gets_403(client, user):
    headers = {"X-Dev-User": user}
    assert (await client.get("/api/consumer/search", params={"q": "volume"}, headers=headers)).status_code == 403
    assert (await client.get("/api/consumer/requests", headers=headers)).status_code == 403
    response = await client.post("/api/consumer/requests", headers=headers,
                                 json={"query": "volume", "element_keys": ["volume"], "terms": []})
    assert response.status_code == 403


# ---- 422: refused input, never a 500


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"query": "esg", "element_keys": ["esg_score"]}, "esg_score"),  # unknown element key
        ({"query": "nothing"}, "Tick at least one"),  # nothing to request
        ({"query": "closing price", "terms": ["Closing Price"]}, "closing price"),  # a glossary phrase
        ({"query": "  ", "element_keys": ["volume"]}, None),  # blank query
        ({"query": "v" * 501, "element_keys": ["volume"]}, None),
        ({"query": "volume", "purpose": "p" * 2001, "element_keys": ["volume"]}, None),
        ({"query": "terms", "terms": [f"term {i}" for i in range(21)]}, None),
        ({"query": "term", "terms": ["t" * 101]}, None),
    ],
    ids=["unknown key", "nothing", "term in glossary", "blank query", "long query", "long purpose", "many terms",
         "long term"],
)
async def test_refused_input_is_422_and_leaves_nothing(client, db_session, body, detail):
    response = await client.post("/api/consumer/requests", headers=RILEY, json=body)
    assert response.status_code == 422, response.text
    if detail:
        assert detail in response.json()["detail"]
    assert ConsumerRequestRepository(db_session).list_for("dev-requester") == []
    assert RequirementRepository(db_session).list() == []


# ---- one transaction


class FailingNotifier:
    """Writes like the in-app notifier, then fails on call number `fail_on`."""

    name = "failing"

    def __init__(self, session, fail_on):
        self._inner, self._fail_on, self.calls = InAppNotifier(session), fail_on, 0

    def notify(self, **message):
        self.calls += 1
        if self.calls == self._fail_on:
            raise RuntimeError("notifier failed on purpose")
        return self._inner.notify(**message)


async def test_a_failed_request_leaves_nothing_behind(client, db_session):
    """Ticked terms send two notifications (backend team, then glossary owner); the second fails, after
    the requirement, its elements and terms and the first notification were written."""
    from apps.api.main import app

    notifier = FailingNotifier(db_session, fail_on=2)
    app.dependency_overrides[get_request_notifier] = lambda: notifier
    with pytest.raises(RuntimeError, match="on purpose"):
        await client.post("/api/consumer/requests", headers=RILEY,
                          json={"query": "volume and esg score", "element_keys": ["volume"], "terms": ["esg score"]})
    assert notifier.calls == 2
    assert RequirementRepository(db_session).list() == []
    assert ConsumerRequestRepository(db_session).list_for("dev-requester") == []
    assert NotificationRepository(db_session).list_for("dev-backend-team") == []
    from sqlalchemy import text

    for table in ("requirement_elements", "requirement_terms"):
        assert db_session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0, table


# ---- phase 3b: the DiscoveryAssistant beside the search (assumptions 38 to 40)


@pytest.fixture
def temporary_blobs(tmp_path):
    from apps.api.deps import get_blob_store_port
    from apps.api.main import app
    from ports.storage.local import LocalBlobStore

    blobs = LocalBlobStore(tmp_path / "blob")
    app.dependency_overrides[get_blob_store_port] = lambda: blobs
    return blobs


async def assist(client, q, headers=RILEY, expect=200):
    response = await client.post("/api/consumer/search/assist", headers=headers, json={"q": q})
    assert response.status_code == expect, response.text
    return response.json()


def _log_rows(db_session):
    from sqlalchemy import text

    return db_session.execute(text("SELECT agent_name FROM proposal_log")).scalars().all()


async def test_search_itself_makes_no_model_call(client, db_session):
    found = await search(client, "closing prices")
    assert found["match"]["terms"] == ["closing prices"]  # unmatched: the assistant's input
    assert _log_rows(db_session) == []


async def test_the_assistant_call_is_logged_without_a_proposal(client, db_session, temporary_blobs):
    from sqlalchemy import text

    body = await assist(client, "closing prices")
    [suggestion] = body["suggestions"]
    assert (suggestion["term"], suggestion["element_key"], suggestion["element_name"]) == (
        "closing prices", "close_price", "Close price")
    assert [h["product"]["code"] for h in suggestion["products"]]  # the demo products that hold close price
    assert _log_rows(db_session) == ["DiscoveryAssistant"]
    assert db_session.execute(text("SELECT count(*) FROM proposals")).scalar_one() == 0
    log_input = db_session.execute(text("SELECT input_ref FROM proposal_log")).scalar_one()
    assert temporary_blobs.exists(log_input)


async def test_the_assistant_is_for_the_requester_only(client, temporary_blobs):
    await assist(client, "closing prices", headers={"X-Dev-User": "dev-backend-team"}, expect=403)
    await assist(client, "closing prices", headers={}, expect=401)
    response = await client.post("/api/consumer/search/assist", headers=RILEY, json={"q": "x" * 501})
    assert response.status_code == 422
