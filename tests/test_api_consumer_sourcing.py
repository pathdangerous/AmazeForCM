"""Consumer API for the custom data product, part 3a: the requirement page and "Find a source".

GET  /api/consumer/requirements/{id}               status, the requester's message, source, last fetch
POST /api/consumer/requirements/{id}/find-source   Known source?; for a known source the selection,
                                                   then the first fetch in its own transaction

The requester only, and only for their own requirement (another's is 404). A requirement that is not
defined gives 409. Phase 3 plan, assumptions 2, 9, 13, 14 and 43.
"""

import pytest

from apps.api.deps import get_blob_store_port
from core.repositories.consumer_requests import ConsumerRequestRepository
from core.repositories.requirements import RequirementRepository
from core.seed import seed_demo, seed_glossary
from ports.config import Settings
from ports.storage.local import LocalBlobStore
from tests.conftest import running_app

pytestmark = pytest.mark.anyio

RILEY = {"X-Dev-User": "dev-requester"}
BLAKE = {"X-Dev-User": "dev-backend-team"}
ONBOARDING = "Your data is getting onboarded; we will notify you once it is ready."
RAISED = "Requirement raised; the backend team will look for a source."
SCOPE_TO_CONFIRM = "No known source has this data yet. Confirm where Amaze should look for it."
NO_FIT = "No source fits this request. The backend team has been told."
# Approved at checkpoint 5 (2026-11-15), word for word.
SCOPE_NOT_CONFIRMED = "You didn't confirm where to look. Add a note and ask again."
SCOPE_CONFIRMED = "Thanks. Amaze is now finding sources for this data."
PICK_A_SOURCE = "Pick a source and accept its conditions to start onboarding."
ALL_DECLINED = "You declined every listed source. Add a note and ask Amaze to look again."
VENDOR, BACKUP = "syn_vendor_eod_prices", "syn_backup_eod_prices"


@pytest.fixture
async def client(db_session, migrated_engine, tmp_path):
    """The real catalogue and sample files; a temporary blob store, so no file lands in data/blob."""
    from apps.api.main import app

    seed_glossary(db_session)
    seed_demo(db_session)
    db_session.commit()
    blobs = LocalBlobStore(tmp_path / "blob")
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None), session=db_session) as c:
        app.dependency_overrides[get_blob_store_port] = lambda: blobs
        c.blobs = blobs
        yield c


async def raise_requirement(client, query, element_keys=(), terms=(), purpose=""):
    response = await client.post("/api/consumer/requests", headers=RILEY, json={
        "query": query, "element_keys": list(element_keys), "terms": list(terms), "purpose": purpose,
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["routing"]["outcome"] == "raise_requirement"
    return body["requirement"]["requirement"]["id"]


async def find_source(client, requirement_id, headers=RILEY, expect=200):
    response = await client.post(f"/api/consumer/requirements/{requirement_id}/find-source", headers=headers)
    assert response.status_code == expect, response.text
    return response.json()


async def page(client, requirement_id, headers=RILEY, expect=200):
    response = await client.get(f"/api/consumer/requirements/{requirement_id}", headers=headers)
    assert response.status_code == expect, response.text
    return response.json()


async def test_done_when_a_known_source_is_fetched_and_the_requester_sees_onboarding(client):
    """3a done-when, item 2: row A of the plan."""
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    before = await page(client, requirement_id)
    assert (before["status"], before["message"], before["next_action"]) == ("defined", RAISED, "find_source")

    found = await find_source(client, requirement_id)
    assert (found["check"]["outcome"], found["check"]["source"]["code"]) == ("known_source", "syn_exchange_volumes")
    assert found["fetch"]["outcome"] == "fetched"
    assert found["message"] == ONBOARDING

    after = await page(client, requirement_id)
    assert (after["status"], after["message"], after["next_action"]) == ("onboarding", ONBOARDING, None)
    assert (after["source"]["code"], after["source"]["feed_code"]) == ("syn_exchange_volumes", "syn_exchange_volumes")
    assert after["source"]["conditions"]["licence"] == "Synthetic exchange data licence"
    assert after["last_fetch"]["outcome"] == "fetched"
    assert client.blobs.get(found["fetch"]["blob_ref"]).startswith(b"instr_id,trade_date,traded_qty")

    feeds = (await client.get("/api/amaze/feeds", headers=BLAKE)).json()
    assert [(f["code"], f["stage"], f["requirement_id"]) for f in feeds] == [
        ("syn_exchange_volumes", "candidate", requirement_id),
    ]


async def test_a_known_source_that_does_not_answer_waits(client):
    """Row B: the selection stands; the requirement waits as source_selected."""
    requirement_id = await raise_requirement(client, "accrued interest", ["accrued_interest"])
    found = await find_source(client, requirement_id)
    assert (found["check"]["outcome"], found["fetch"]["outcome"]) == ("known_source", "source_down")
    message = "A source was found: Accrued interest feed. It did not answer; Amaze tries again every day."
    assert found["message"] == message
    after = await page(client, requirement_id)
    assert (after["status"], after["message"], after["last_fetch"]["outcome"]) == ("source_selected", message,
                                                                                  "source_down")


@pytest.mark.parametrize(
    ("query", "element_keys", "terms", "reason"),
    [
        ("open price", ["open_price"], [], "no_known_source_covers"),  # row C
        ("esg score", [], ["esg score"], "terms_ticked"),  # row D
    ],
)
async def test_no_known_source_proposes_a_scope(client, query, element_keys, terms, reason):
    """3b (assumption 9): no_known_source, then S1 in the same request and the same transaction."""
    requirement_id = await raise_requirement(client, query, element_keys, terms)
    found = await find_source(client, requirement_id)
    assert (found["check"]["outcome"], found["check"]["reason"], found["fetch"]) == ("no_known_source", reason, None)
    assert found["message"] == SCOPE_TO_CONFIRM
    assert (found["scope"]["visibility"], found["scope"]["where_to_search"]) == (
        "public", ["exchanges", "market_data_vendors", "public_websites"])
    after = await page(client, requirement_id)
    assert (after["status"], after["source"], after["next_action"]) == (
        "awaiting_scope_confirmation", None, "confirm_scope")
    assert after["proposed_scope"]["proposal_id"] == found["scope"]["proposal_id"]


async def test_find_source_needs_status_defined(client):
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, requirement_id)
    second = await find_source(client, requirement_id, expect=409)
    assert "onboarding" in second["detail"]


async def test_another_requesters_requirement_is_404(client, db_session):
    other = RequirementRepository(db_session).create(requester_id="dev-someone-else", title="volume",
                                                     element_keys=["volume"])
    db_session.commit()
    await page(client, other.id, expect=404)
    await find_source(client, other.id, expect=404)
    assert RequirementRepository(db_session).get(other.id).status == "defined"
    await page(client, "00000000-0000-4000-8000-000000000000", expect=404)


async def test_backend_users_and_no_user(client):
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await page(client, requirement_id, headers=BLAKE, expect=403)
    await find_source(client, requirement_id, headers=BLAKE, expect=403)
    await page(client, requirement_id, headers={}, expect=401)
    await find_source(client, requirement_id, headers={}, expect=401)


async def test_my_requests_links_each_raised_requirement(client, db_session):
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, requirement_id)
    mine = (await client.get("/api/consumer/requests", headers=RILEY)).json()
    assert [(r["requirement_id"], r["requirement_status"]) for r in mine] == [(requirement_id, "onboarding")]
    assert ConsumerRequestRepository(db_session).list_for("dev-requester")[0].result["message"] == RAISED


# ================================================================ part 3b: scope, sources, pick, release


async def post(client, requirement_id, action, body=None, headers=RILEY, expect=200):
    response = await client.post(f"/api/consumer/requirements/{requirement_id}/{action}", headers=headers,
                                 json=body if body is not None else {})
    assert response.status_code == expect, response.text
    return response.json()


async def scoped_and_listed(client, query="open price", element_keys=("open_price",), terms=(), purpose=""):
    """Raise, Find a source (S1 proposes), confirm (scoped), list (S2). Returns (id, listed by code)."""
    requirement_id = await raise_requirement(client, query, element_keys, terms, purpose)
    await find_source(client, requirement_id)
    decided = await post(client, requirement_id, "scope/decision", {"decision": "approved"})
    assert (decided["result"]["outcome"], decided["message"]) == ("scoped", SCOPE_CONFIRMED)
    listed = await post(client, requirement_id, "sources")
    if listed["result"]["outcome"] == "sources_listed":
        assert listed["message"] == PICK_A_SOURCE
    return requirement_id, {c["source"]["code"]: c for c in listed["result"].get("candidates", [])}, listed


async def test_done_when_picking_a_source_hands_a_fetched_file_to_step_02(client):
    """BUILD_PLAN.md phase 3 done-when; 3b done-when item 3; row E of the plan."""
    requirement_id, listed, response = await scoped_and_listed(client)
    assert response["result"]["outcome"] == "sources_listed"
    assert list(listed) == [BACKUP, VENDOR]  # the mock: both cover open price; the backup declares fewer fields
    vendor = listed[VENDOR]
    assert vendor["source"]["conditions"]["external_approvals"] == [
        "A signed data agreement with the vendor, held by your desk"]
    assert (await page(client, requirement_id))["next_action"] == "pick_source"

    missing = await post(client, requirement_id, "pick", {"proposal_id": vendor["proposal_id"]})
    assert (missing["result"]["outcome"], missing["result"]["reason"], missing["fetch"]) == (
        "approval_missing", "not_accepted", None)
    assert missing["message"] == ("Vendor end-of-day prices can be used only under these conditions. "
                                  "Accept them to continue.")
    assert missing["result"]["conditions"] == vendor["source"]["conditions"]
    feeds = (await client.get("/api/amaze/feeds", headers=BLAKE)).json()
    assert feeds == [] and (await page(client, requirement_id))["status"] == "awaiting_source_choice"

    picked = await post(client, requirement_id, "pick", {"proposal_id": vendor["proposal_id"],
                                                         "accepted_conditions": vendor["source"]["conditions"]})
    assert (picked["result"]["outcome"], picked["result"]["feed_code"]) == ("source_picked", VENDOR)
    assert picked["fetch"]["outcome"] == "fetched"
    assert picked["message"] == ONBOARDING
    assert client.blobs.get(picked["fetch"]["blob_ref"])  # the stored file
    after = await page(client, requirement_id)
    assert (after["status"], after["message"], after["source"]["code"]) == ("onboarding", ONBOARDING, VENDOR)
    feeds = (await client.get("/api/amaze/feeds", headers=BLAKE)).json()
    assert [(f["code"], f["stage"], f["requirement_id"]) for f in feeds] == [(VENDOR, "candidate", requirement_id)]


async def test_row_f_a_ticked_term_finds_a_source_by_its_other_field(client):
    requirement_id, listed, _ = await scoped_and_listed(client, "esg score", (), ("esg score",))
    [esg] = listed.values()
    assert (esg["source"]["code"], esg["coverage"]["terms"]) == ("syn_open_esg_scores", ["esg score"])
    picked = await post(client, requirement_id, "pick", {"proposal_id": esg["proposal_id"],
                                                         "accepted_conditions": esg["source"]["conditions"]})
    assert (picked["result"]["outcome"], picked["fetch"]["outcome"]) == ("source_picked", "fetched")


async def test_row_g_a_private_scope_with_no_source_is_no_fit(client):
    requirement_id, listed, response = await scoped_and_listed(client, "yield to maturity", ("yield_to_maturity",),
                                                               purpose="internal desk data")
    assert (await page(client, requirement_id))["scope"]["visibility"] == "private"
    assert (response["result"]["outcome"], response["result"]["reason"], listed) == ("no_fit", "nothing_in_scope", {})
    assert response["message"] == NO_FIT
    after = await page(client, requirement_id)
    assert (after["status"], after["message"], after["next_action"]) == ("no_source_found", NO_FIT, "ask_for_scope")


async def test_row_h_a_rejected_scope_and_asking_again_with_a_note(client):
    requirement_id = await raise_requirement(client, "yield to maturity", ["yield_to_maturity"])
    first = await find_source(client, requirement_id)
    assert first["scope"]["visibility"] == "public"
    rejected = await post(client, requirement_id, "scope/decision", {"decision": "rejected",
                                                                     "note": "It is internal desk data"})
    assert (rejected["result"]["outcome"], rejected["result"]["note"]) == ("scope_not_confirmed",
                                                                           "It is internal desk data")
    assert rejected["message"] == SCOPE_NOT_CONFIRMED
    assert (await page(client, requirement_id))["next_action"] == "ask_for_scope"
    again = await post(client, requirement_id, "scope", {"note": "It is internal desk data"})
    assert again["result"]["visibility"] == "private"  # the note holds a private word
    assert again["result"]["proposal_id"] != first["scope"]["proposal_id"]
    assert (await page(client, requirement_id))["status"] == "awaiting_scope_confirmation"


async def test_find_source_and_scope_return_s1s_no_fit(client):
    """Independent review, fix 6: S1's typed result is returned, no_fit included (a proposed scope with no
    channel to search, review fix 5). A catalogue with no private source makes a private scope empty."""
    from apps.api.deps import get_source_catalogue
    from apps.api.main import app
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue

    public_only = SourceCatalogue([e for e in SourceCatalogue.load(DEFAULT_CATALOGUE).entries
                                   if e.visibility == "public"])
    app.dependency_overrides[get_source_catalogue] = lambda: public_only

    internal = await raise_requirement(client, "yield to maturity", ["yield_to_maturity"], purpose="internal desk data")
    found = await find_source(client, internal)
    assert (found["scope"]["outcome"], found["scope"]["reason"]) == ("no_fit", "no_channel_in_scope")
    assert found["message"] == NO_FIT

    asked = await raise_requirement(client, "yield to maturity", ["yield_to_maturity"])
    assert (await find_source(client, asked))["scope"]["visibility"] == "public"
    await post(client, asked, "scope/decision", {"decision": "rejected", "note": "internal"})
    again = await post(client, asked, "scope", {"note": "It is internal desk data"})
    assert (again["result"]["outcome"], again["result"]["reason"]) == ("no_fit", "no_channel_in_scope")
    assert (await page(client, asked))["status"] == "no_source_found"


async def test_row_j_release_a_source_that_did_not_answer_and_pick_again(client):
    requirement_id, listed, _ = await scoped_and_listed(client)
    backup, vendor = listed[BACKUP], listed[VENDOR]
    down = await post(client, requirement_id, "pick", {"proposal_id": backup["proposal_id"],
                                                       "accepted_conditions": backup["source"]["conditions"]})
    assert (down["result"]["outcome"], down["fetch"]["outcome"]) == ("source_picked", "source_down")
    assert (await page(client, requirement_id))["can_release"] is True

    released = await post(client, requirement_id, "release")
    assert (released["result"]["reason"], released["result"]["status"], released["result"]["version"]) == (
        "requester_asked", "awaiting_source_choice", 2)
    assert released["message"] == "You released Backup end-of-day prices. Pick another source."
    after = await page(client, requirement_id)
    assert (after["message"], [c["source"]["code"] for c in after["listed"]]) == (released["message"], [VENDOR])

    picked = await post(client, requirement_id, "pick", {"proposal_id": vendor["proposal_id"],
                                                         "accepted_conditions": vendor["source"]["conditions"]})
    assert (picked["result"]["outcome"], picked["fetch"]["outcome"]) == ("source_picked", "fetched")
    history = (await page(client, requirement_id))["choices"]
    assert [(c["version"], c["action"], c["source_code"]) for c in history] == [
        (1, "selected", BACKUP), (2, "released", BACKUP), (3, "selected", VENDOR)]


async def test_row_k_a_released_known_source_then_no_fit(client):
    requirement_id = await raise_requirement(client, "accrued interest", ["accrued_interest"])
    await find_source(client, requirement_id)  # known source, source_down (row B)
    released = await post(client, requirement_id, "release")
    assert released["result"]["status"] == "no_source_found"
    assert released["message"] == ("You released Accrued interest feed. No other source is listed. "
                                   "Add a note and ask for a new scope.")
    await post(client, requirement_id, "scope", {"note": "Any public source is fine"})
    await post(client, requirement_id, "scope/decision", {"decision": "approved"})
    listed = await post(client, requirement_id, "sources")
    assert (listed["result"]["outcome"], listed["message"]) == ("no_fit", NO_FIT)  # the only one was released


async def test_declining_listed_sources(client):
    requirement_id, listed, _ = await scoped_and_listed(client)
    first = await post(client, requirement_id, "decline", {"proposal_id": listed[VENDOR]["proposal_id"],
                                                           "note": "No vendor agreement"})
    assert (first["result"]["status"], first["result"]["remaining"]) == ("awaiting_source_choice", 1)
    last = await post(client, requirement_id, "decline", {"proposal_id": listed[BACKUP]["proposal_id"]})
    assert last["result"]["status"] == "no_source_found"
    assert last["message"] == ALL_DECLINED


async def test_a_wrong_status_is_409(client):
    requirement_id, listed, _ = await scoped_and_listed(client)
    await post(client, requirement_id, "scope/decision", {"decision": "approved"}, expect=409)
    await post(client, requirement_id, "sources", expect=409)
    await post(client, requirement_id, "scope", {"note": "x"}, expect=409)
    await post(client, requirement_id, "release", expect=409)
    vendor = listed[VENDOR]
    await post(client, requirement_id, "pick", {"proposal_id": vendor["proposal_id"],
                                                "accepted_conditions": vendor["source"]["conditions"]})
    await post(client, requirement_id, "release", expect=409)  # onboarding: its file is with step 02
    await post(client, requirement_id, "pick", {"proposal_id": listed[BACKUP]["proposal_id"]}, expect=409)


async def test_another_requesters_requirement_or_proposal_is_404(client, db_session):
    requirement_id, listed, _ = await scoped_and_listed(client)
    other = RequirementRepository(db_session).create(requester_id="dev-other-requester", title="T")
    proposal = {"proposal_id": listed[VENDOR]["proposal_id"]}
    bodies = {"scope": {"note": "x"}, "scope/decision": {"decision": "approved"}, "sources": {}, "pick": proposal,
              "decline": proposal, "release": {}}
    for action, body in bodies.items():
        await post(client, other.id, action, body, expect=404)
    assert RequirementRepository(db_session).get(other.id).status == "defined"
    second, _, _ = await scoped_and_listed(client, "high price", ("high_price",))
    await post(client, second, "pick", {"proposal_id": listed[VENDOR]["proposal_id"]}, expect=404)
    await post(client, requirement_id, "pick", {"proposal_id": "00000000-0000-4000-8000-000000000000"}, expect=404)


async def test_picking_a_source_that_left_the_catalogue_is_409(client):
    """Review fix 4: the proposal still exists, but its source is gone from the catalogue. 409, not 500."""
    from apps.api.deps import get_source_catalogue
    from apps.api.main import app
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue

    requirement_id, listed, _ = await scoped_and_listed(client)
    vendor = listed[VENDOR]
    without_vendor = SourceCatalogue([e for e in SourceCatalogue.load(DEFAULT_CATALOGUE).entries if e.code != VENDOR])
    app.dependency_overrides[get_source_catalogue] = lambda: without_vendor
    refused = await post(client, requirement_id, "pick", {"proposal_id": vendor["proposal_id"],
                                                          "accepted_conditions": vendor["source"]["conditions"]},
                         expect=409)
    assert VENDOR in refused["detail"]
    assert (await page(client, requirement_id))["status"] == "awaiting_source_choice"


async def test_refused_input_is_422(client):
    requirement_id = await raise_requirement(client, "open price", ["open_price"])
    await find_source(client, requirement_id)
    await post(client, requirement_id, "scope/decision", {"decision": "maybe"}, expect=422)
    await post(client, requirement_id, "scope/decision", {"decision": "approved", "extra": 1}, expect=422)


async def test_backend_users_get_403_on_every_3b_endpoint(client):
    requirement_id = await raise_requirement(client, "open price", ["open_price"])
    for action in ("scope", "scope/decision", "sources", "pick", "decline", "release"):
        await post(client, requirement_id, action, headers=BLAKE, expect=403)


async def test_a_failed_model_call_writes_nothing_and_keeps_the_status(client, db_session):
    """A stub provider is an error, never an outcome (assumption 26): 503 and status defined."""
    from apps.api.deps import get_llm_provider_port
    from apps.api.main import app
    from core.repositories.tables import ProposalLogEntry
    from ports.llm.azure_openai import AzureOpenAIProvider

    requirement_id = await raise_requirement(client, "open price", ["open_price"])
    app.dependency_overrides[get_llm_provider_port] = lambda: AzureOpenAIProvider()
    response = await client.post(f"/api/consumer/requirements/{requirement_id}/find-source", headers=RILEY)
    assert response.status_code == 503
    assert (await page(client, requirement_id))["status"] == "defined"
    assert db_session.query(ProposalLogEntry).count() == 0


# ---------------------------------------------------------------- phase 4: a registered feed; the sign-off on the page


async def _backend(client, feed_code, *steps):
    """Run the Amaze steps on a feed as Blake Backend; each step is (action, body)."""
    feeds = (await client.get("/api/amaze/feeds", headers=BLAKE)).json()
    feed_id = next(f["id"] for f in feeds if f["code"] == feed_code)
    for action, body in steps:
        response = await client.post(f"/api/amaze/feeds/{feed_id}/{action}", headers=BLAKE, json=body)
        assert response.status_code == 200, response.text
    return feed_id


ACCEPT_AND_REGISTER = (("profile", {}), ("profile/summary", {}), ("signoff", {"outcome": "meets"}),
                       ("register", {"owner_id": "dev-data-owner", "visibility": "open"}))


async def test_row_p_find_source_links_a_registered_feed_without_a_fetch(client, db_session):
    """Phase 4 plan, row P: duplicate_linked. Nothing is fetched, profiled or registered again."""
    from core.repositories.source_fetches import SourceFetchRepository

    first = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, first)
    feed_id = await _backend(client, "syn_exchange_volumes", *ACCEPT_AND_REGISTER)
    fetches = len(SourceFetchRepository(db_session).list_for_feed(feed_id))

    later = await raise_requirement(client, "volume again", ["volume"])
    found = await find_source(client, later)
    assert (found["check"]["outcome"], found["fetch"]) == ("known_source", None)
    assert (found["registration"]["outcome"], found["registration"]["feed_code"]) == (
        "duplicate_linked", "syn_exchange_volumes")
    assert found["message"] == ONBOARDING and (await page(client, later))["status"] == "onboarding"
    assert len(SourceFetchRepository(db_session).list_for_feed(feed_id)) == fetches


async def test_a_pick_of_a_registered_source_links_without_a_fetch(client):
    first, listed, _ = await scoped_and_listed(client)
    vendor = listed[VENDOR]
    await post(client, first, "pick", {"proposal_id": vendor["proposal_id"],
                                       "accepted_conditions": vendor["source"]["conditions"]})
    await _backend(client, VENDOR, *ACCEPT_AND_REGISTER)

    later, listed, _ = await scoped_and_listed(client, "open price again")
    vendor = listed[VENDOR]
    picked = await post(client, later, "pick", {"proposal_id": vendor["proposal_id"],
                                                "accepted_conditions": vendor["source"]["conditions"]})
    assert (picked["result"]["outcome"], picked["fetch"], picked["registration"]["outcome"]) == (
        "source_picked", None, "duplicate_linked")
    assert (picked["message"], (await page(client, later))["status"]) == (ONBOARDING, "onboarding")


async def test_the_requirement_page_shows_the_gaps_under_the_onboarding_message(client):
    requirement_id = await raise_requirement(client, "volume", ["volume"])
    await find_source(client, requirement_id)
    await _backend(client, "syn_exchange_volumes", ("profile", {}), ("profile/summary", {}),
                   ("signoff", {"outcome": "meets_with_gaps", "gaps": ["Volumes before 2026-11-02 are missing"]}))
    shown = await page(client, requirement_id)
    assert (shown["message"], shown["signoff_note"]) == (
        ONBOARDING, "The backend team accepted Exchange daily volumes with gaps: Volumes before 2026-11-02 are missing.")


async def test_a_rejected_source_is_no_longer_listed_and_its_pick_is_409(client):
    """Listed before its rejection: the page no longer offers it, and a pick is 409. (Find a source never
    offers a rejected known source; its 409 is a race, test_known_source.py's step test.)"""
    first, listed, _ = await scoped_and_listed(client)
    later, later_listed, _ = await scoped_and_listed(client, "open price again")
    vendor = listed[VENDOR]
    await post(client, first, "pick", {"proposal_id": vendor["proposal_id"],
                                       "accepted_conditions": vendor["source"]["conditions"]})
    await _backend(client, VENDOR, ("profile", {}), ("profile/summary", {}),
                   ("signoff", {"outcome": "rejected", "reasons": ["No vendor agreement on file"]}))
    assert [c["source"]["code"] for c in (await page(client, later))["listed"]] == [BACKUP]
    await post(client, later, "pick", {"proposal_id": later_listed[VENDOR]["proposal_id"],
                                       "accepted_conditions": vendor["source"]["conditions"]}, expect=409)


@pytest.mark.parametrize("reason", ["timeout", "rate_limit", "provider_error"])
@pytest.mark.parametrize("action", ["find-source", "assist"])
async def test_a_provider_that_does_not_answer_gives_503_and_writes_nothing(client, db_session, reason, action):
    """Independent review, fix 4: a timeout, a rate limit or a provider error is LLMUnavailable, an
    expected case: 503 with its reason, mapped in one place (apps/api/main.py), never a bare 500."""
    from apps.api.deps import get_llm_provider_port
    from apps.api.main import app
    from core.repositories.tables import ProposalLogEntry
    from ports.llm.base import LLMUnavailable

    class FailingMock:
        name = "mock"

        def complete(self, **kwargs):
            raise LLMUnavailable(reason, "synthetic failure")

    requirement_id = await raise_requirement(client, "open price", ["open_price"])
    app.dependency_overrides[get_llm_provider_port] = lambda: FailingMock()
    if action == "find-source":
        response = await client.post(f"/api/consumer/requirements/{requirement_id}/find-source", headers=RILEY)
    else:
        response = await client.post("/api/consumer/search/assist", headers=RILEY, json={"q": "closing prices"})
    assert response.status_code == 503, response.text
    assert response.json()["reason"] == reason
    assert (await page(client, requirement_id))["status"] == "defined"
    assert db_session.query(ProposalLogEntry).count() == 0
