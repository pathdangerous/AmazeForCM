"""Step 17 Discover (core/steps/s17_discover.py): deterministic search, one typed result per product.

Outcomes (CLAUDE.md): found; not_entitled_visible (shown, with the route to access). No hits is an
empty list, not an outcome (decision 14). Phase 2 plan, assumptions 14 to 17.
"""

import pytest

from core.repositories.glossary import GlossaryRepository
from core.repositories.grants import ProductGrantRepository
from core.steps.s17_discover import Found, NotEntitledVisible, discover

NAMES = {"dev-data-owner": "Devon Owner"}


@pytest.fixture
def search(db_session, glossary):
    def _search(query, *, user_id="dev-requester", roles=("requester",)):
        return discover(db_session, query=query, user_id=user_id, roles=list(roles),
                        display_name=lambda uid: NAMES.get(uid, uid))

    return _search


def hits(result):
    return [(h.outcome, h.product.code) for h in result.hits]


def test_found_for_an_open_product(search, make_product):
    make_product("daily_close", name="Daily close prices", element_keys=["price_date", "close_price"])
    result = search("close price")
    assert hits(result) == [("found", "daily_close")]
    hit = result.hits[0]
    assert isinstance(hit, Found)
    assert (hit.product.name, hit.product.domain, hit.product.element_keys) == (
        "Daily close prices", "pricing", ["close_price", "price_date"],
    )
    assert [e.key for e in result.match.elements] == ["close_price"]


def test_not_entitled_visible_carries_the_route(search, make_product):
    make_product("bond_reference", name="Bond reference data", domain="security_master", visibility="restricted",
                 element_keys=["isin", "maturity_date"])
    result = search("maturity date")
    assert hits(result) == [("not_entitled_visible", "bond_reference")]
    hit = result.hits[0]
    assert isinstance(hit, NotEntitledVisible)
    assert hit.route == "Ask Devon Owner for access to Bond reference data"


def test_a_granted_user_and_the_owner_find_a_restricted_product(search, make_product, db_session):
    product = make_product("bond_reference", domain="security_master", visibility="restricted",
                           element_keys=["isin"])
    assert hits(search("isin", user_id="dev-data-owner", roles=["data_owner"])) == [("found", "bond_reference")]
    ProductGrantRepository(db_session).record(product_id=product.id, subject_type="role", subject="requester",
                                              action="grant", decided_by="dev-data-owner")
    assert hits(search("isin")) == [("found", "bond_reference")]


def test_no_hits_is_an_empty_list(search, make_product):
    make_product("daily_close", element_keys=["close_price"])
    result = search("volume")
    assert result.hits == []
    assert [e.key for e in result.match.elements] == ["volume"]


@pytest.mark.parametrize("query", ["", "   ", "the data please"])
def test_a_blank_query_returns_an_empty_match_and_no_hits(search, make_product, query):
    make_product("daily_close", element_keys=["close_price"])
    result = search(query)
    assert result.hits == []
    assert (result.match.elements, result.match.terms, result.match.domains) == ([], [], [])


def test_a_domain_word_filters_the_hits(search, make_product):
    make_product("prices", name="Prices", element_keys=["instrument_id", "close_price"])
    make_product("quotes", name="Quotes", element_keys=["instrument_id", "bid_price"])
    make_product("reference", name="Reference", domain="security_master", element_keys=["instrument_id", "isin"])
    assert hits(search("instrument id")) == [("found", "prices"), ("found", "quotes"), ("found", "reference")]
    assert hits(search("pricing instrument id")) == [("found", "prices"), ("found", "quotes")]
    # only domain words: every product in those domains
    assert hits(search("pricing")) == [("found", "prices"), ("found", "quotes")]
    assert hits(search("security master")) == [("found", "reference")]


def test_a_word_of_an_unmatched_term_matches_a_whole_word_of_the_name_or_description(search, make_product):
    make_product("quotes", name="Bid and ask quotes", element_keys=["bid_price"])
    make_product("eod", name="End of day", description="Settlement snapshot taken after the close.",
                 element_keys=["close_price"])
    assert hits(search("quotes")) == [("found", "quotes")]
    assert hits(search("settlement snapshot")) == [("found", "eod")]
    assert hits(search("quote")) == []  # a whole word only: no stemming


def test_a_candidate_element_also_finds_products(search, make_product, db_session):
    """An ambiguous phrase (two elements) finds the products holding either candidate."""
    GlossaryRepository(db_session).add_seed_element(
        key="synthetic_close_flag", name="Synthetic close flag", domain="pricing", data_type="boolean",
        definition="Synthetic element sharing the alias close.", aliases=["close"],
    )
    make_product("daily_close", name="Daily close", element_keys=["close_price"])
    result = search("close")
    assert result.match.elements == []
    assert [a.candidates for a in result.match.ambiguous] == [["close_price", "synthetic_close_flag"]]
    assert hits(result) == [("found", "daily_close")]


def test_hits_are_ordered_by_matched_elements_then_name_then_code(search, make_product):
    make_product("b_one", name="Same name", element_keys=["close_price"])
    make_product("a_one", name="Same name", element_keys=["close_price"])
    make_product("both", name="Zed", element_keys=["close_price", "volume"])
    make_product("earlier_name", name="Alpha", element_keys=["volume"])
    assert [h.product.code for h in search("close price and volume").hits] == [
        "both", "earlier_name", "a_one", "b_one",
    ]
