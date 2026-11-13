"""request_data (core/steps/request_data.py): one "Request this data", end to end inside the caller's
transaction: input checks, routing, step 01 when a requirement is raised, notifications, the request
log row, and the requester's message and notes. Phase 2 plan, assumptions 3, 24, 29, 32 and 34.
"""

import pytest

from core.repositories.consumer_requests import ConsumerRequestRepository
from core.repositories.errors import UnknownGlossaryElement
from core.repositories.notifications import NotificationRepository
from core.repositories.requirements import RequirementRepository
from core.steps.errors import NothingToRequest, TermInGlossary
from core.steps.request_data import request_data
from ports.notify.inapp import InAppNotifier

NAMES = {"dev-requester": "Riley Requester", "dev-data-owner": "Devon Owner"}
RECIPIENTS = {"backend_team": ["dev-backend-team"], "glossary_owner": ["dev-glossary-owner"]}
RAISED = "Requirement raised; the backend team will look for a source."


@pytest.fixture
def ask(db_session, glossary):
    def _ask(query, element_keys=(), terms=(), purpose=""):
        return request_data(
            db_session, requester_id="dev-requester", roles=["requester"], query=query, purpose=purpose,
            element_keys=list(element_keys), terms=list(terms), notifier=InAppNotifier(db_session),
            recipients=RECIPIENTS, display_name=lambda uid: NAMES.get(uid, uid),
        )

    return _ask


@pytest.fixture
def demo_like(make_product):
    make_product("daily_close", name="Daily close prices", element_keys=["instrument_id", "price_date", "close_price"])
    make_product("bid_ask", name="Bid and ask quotes", element_keys=["instrument_id", "price_date", "bid_price"])
    make_product("bond_reference", name="Bond reference data", domain="security_master", visibility="restricted",
                 element_keys=["isin", "maturity_date"])


def subjects(db_session, user_id):
    return [n.subject for n in NotificationRepository(db_session).list_for(user_id)]


def logged(db_session, result):
    return ConsumerRequestRepository(db_session).get_for("dev-requester", result.request_id)


def test_raise_requirement_message_and_backend_notification(ask, db_session):
    result = ask("volume", ["volume"])
    assert result.routing.outcome == "raise_requirement"
    assert result.requirement.outcome == "defined"
    assert result.message == RAISED
    assert result.notes == []
    assert subjects(db_session, "dev-backend-team") == ["New requirement: volume"]
    row = logged(db_session, result)
    assert (row.outcome, row.requirement_id, row.element_keys, row.terms) == (
        "raise_requirement", result.requirement.requirement.id, ["volume"], [],
    )
    assert row.result["message"] == RAISED


def test_reuse_entitled(ask, demo_like, db_session):
    result = ask("close price", ["close_price"])
    assert (result.routing.outcome, result.requirement) == ("reuse", None)
    assert result.message == "This data is available in Daily close prices."
    row = logged(db_session, result)
    assert (row.outcome, row.product_codes, row.requirement_id) == ("reuse", ["daily_close"], None)
    assert subjects(db_session, "dev-backend-team") == []
    assert RequirementRepository(db_session).list() == []


def test_reuse_not_entitled_carries_the_route(ask, demo_like):
    assert ask("maturity date", ["maturity_date"]).message == (
        "This data is in Bond reference data. Access restricted. Ask Devon Owner for access to Bond reference data."
    )


def test_compose_names_the_products(ask, demo_like, db_session):
    result = ask("close price and bid price", ["close_price", "bid_price"])
    assert result.message == (
        "This data can be composed from Bid and ask quotes and Daily close prices. "
        "Composition is not available in the PoC yet."
    )
    assert logged(db_session, result).product_codes == ["bid_ask", "daily_close"]


def test_compose_of_three_products_lists_them_with_commas(ask, demo_like):
    result = ask("close price, bid price, isin", ["close_price", "bid_price", "isin"])
    assert result.message.startswith(
        "This data can be composed from Bid and ask quotes, Bond reference data and Daily close prices."
    )
    assert "Already available: ISIN in Bond reference data. Ask Devon Owner for access to Bond reference data." in result.notes


def test_partial_cover_notes_the_covering_product(ask, demo_like, db_session):
    result = ask("close price and volume", ["close_price", "volume"])
    assert (result.routing.outcome, result.message) == ("raise_requirement", RAISED)
    assert result.notes == ["Already available: Close price in Daily close prices."]
    assert result.requirement.requirement.elements == ["volume"]
    assert logged(db_session, result).product_codes == ["daily_close"]


def test_ticked_terms_raise_a_requirement_and_note_the_glossary_owner(ask, demo_like, db_session):
    result = ask("close price and esg score", ["close_price"], ["esg score"])
    assert (result.routing.outcome, result.requirement.outcome) == ("raise_requirement", "element_not_in_glossary")
    assert result.notes == [
        "Already available: Close price in Daily close prices.",
        "Not in the glossary yet: esg score. The glossary owner has been told.",
    ]
    assert subjects(db_session, "dev-glossary-owner") == ["Terms not in the glossary"]
    assert (result.requirement.requirement.elements, result.requirement.requirement.terms) == ([], ["esg score"])


def test_create_product_notifies_the_backend_team(ask, db_session, approved_proposal):
    from core.repositories.configuration import MappingRepository
    from core.repositories.feeds import FeedRepository
    from core.repositories.loads import LoadRepository

    feeds = FeedRepository(db_session)
    feed = feeds.register(feeds.create_candidate(code="synthetic_feed", name="Synthetic").id, owner_id="dev-data-owner")
    MappingRepository(db_session).add_version(feed_id=feed.id, source_field="Vol", element_key="volume",
                                              proposal_id=approved_proposal(kind="mapping").id)
    loads = LoadRepository(db_session)
    loads.set_status(loads.create(feed_id=feed.id, file_ref="local://raw/x.csv").id, "released")

    result = ask("volume", ["volume"])
    assert (result.routing.outcome, result.requirement) == ("create_product", None)
    assert result.message == "This data is already in Amaze. The backend team has been asked to create a product."
    assert result.notified == ["dev-backend-team"]
    assert subjects(db_session, "dev-backend-team") == ["Product requested from data already in Amaze"]
    assert RequirementRepository(db_session).list() == []


def test_terms_are_cleaned_before_they_are_saved(ask):
    result = ask("esg", terms=["  ESG   Score ", "esg score", "", "carbon-footprint"])
    assert result.requirement.requirement.terms == ["carbon footprint", "esg score"]


def test_a_term_that_is_a_glossary_phrase_is_refused(ask, db_session):
    with pytest.raises(TermInGlossary, match="closing price"):
        ask("closing price", terms=["Closing Price"])
    assert ConsumerRequestRepository(db_session).list_for("dev-requester") == []


def test_unknown_element_and_nothing_to_request(ask, db_session):
    with pytest.raises(UnknownGlossaryElement):
        ask("esg", ["esg_score"])
    with pytest.raises(NothingToRequest):
        ask("anything", terms=["  "])
    assert ConsumerRequestRepository(db_session).list_for("dev-requester") == []
