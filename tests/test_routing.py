"""Routing checks (core/steps/routing.py), in order: reuse, compose (stub), create_product,
raise_requirement. Decisions 3 and 5; phase 2 plan, assumptions 22 to 28.

route() is pure: it gets the catalogue (each product with its elements and access) and the set of
requested elements already in the domains. route_request() builds those from the database.
"""

import pytest

from core.repositories.grants import ProductGrantRepository
from core.steps.errors import NothingToRequest
from core.steps.routing import CatalogueProduct, Compose, CreateProduct, RaiseRequirement, Reuse, route, route_request


def product(code, *elements, entitled=True):
    return CatalogueProduct(code=code, name=code.replace("_", " ").capitalize(), element_keys=list(elements),
                            entitled=entitled, route=None if entitled else f"Ask Devon Owner for access to {code}")


def codes(products):
    return [p.code for p in products]


def test_reuse_when_one_product_covers_every_element():
    result = route(element_keys=["close_price", "price_date"], terms=[],
                   catalogue=[product("daily_close", "close_price", "price_date", "instrument_id")], in_domains=set())
    assert isinstance(result, Reuse)
    assert (result.product.code, result.product.covers, result.product.entitled) == (
        "daily_close", ["close_price", "price_date"], True,
    )


@pytest.mark.parametrize(
    ("catalogue", "expected"),
    [
        ([product("closes", "close_price"), product("bids", "bid_price")], ["bids", "closes"]),
        ([product("closes", "close_price"), product("bids", "bid_price"), product("asks", "ask_price")],
         ["asks", "bids", "closes"]),
    ],
)
def test_compose_when_two_or_three_products_cover_every_element(catalogue, expected):
    asked = sorted({k for p in catalogue for k in p.element_keys})
    result = route(element_keys=asked, terms=[], catalogue=catalogue, in_domains=set())
    assert isinstance(result, Compose)
    assert codes(result.products) == expected


def test_create_product_when_every_uncovered_element_is_in_the_domains():
    result = route(element_keys=["volume", "open_price"], terms=[], catalogue=[],
                   in_domains={"volume", "open_price"})
    assert isinstance(result, CreateProduct)
    assert (result.elements, result.covered_by) == (["open_price", "volume"], [])


def test_partial_cover_with_in_domain_rest_is_create_product():
    result = route(element_keys=["close_price", "volume"], terms=[],
                   catalogue=[product("daily_close", "close_price")], in_domains={"volume"})
    assert isinstance(result, CreateProduct)
    assert result.elements == ["volume"]
    assert [(p.code, p.covers) for p in result.covered_by] == [("daily_close", ["close_price"])]


def test_raise_requirement_for_the_uncovered_elements_only():
    """Every uncovered element, in the domains or not; the covering product is named."""
    result = route(element_keys=["close_price", "open_price", "volume"], terms=[],
                   catalogue=[product("daily_close", "close_price")], in_domains={"open_price"})
    assert isinstance(result, RaiseRequirement)
    assert (result.elements, result.terms, result.in_domains) == (["open_price", "volume"], [], ["open_price"])
    assert codes(result.covered_by) == ["daily_close"]


def test_raise_requirement_when_no_product_holds_anything():
    result = route(element_keys=["volume"], terms=[], catalogue=[product("daily_close", "close_price")],
                   in_domains=set())
    assert isinstance(result, RaiseRequirement)
    assert (result.elements, result.covered_by) == (["volume"], [])


def test_only_terms_raises_a_requirement():
    """Decision 3: if nothing matched, a requirement is raised."""
    result = route(element_keys=[], terms=["esg score"], catalogue=[product("daily_close", "close_price")],
                   in_domains=set())
    assert isinstance(result, RaiseRequirement)
    assert (result.elements, result.terms, result.covered_by) == ([], ["esg score"], [])


def test_ticked_terms_raise_a_requirement_even_when_products_cover_the_elements():
    """Assumption 24: the requirement holds the terms; the products covering the elements are named."""
    result = route(element_keys=["close_price"], terms=["esg score"],
                   catalogue=[product("daily_close", "close_price")], in_domains=set())
    assert isinstance(result, RaiseRequirement)
    assert (result.elements, result.terms) == ([], ["esg score"])
    assert codes(result.covered_by) == ["daily_close"]


def test_nothing_to_request():
    with pytest.raises(NothingToRequest):
        route(element_keys=[], terms=[], catalogue=[], in_domains=set())


@pytest.mark.parametrize(
    ("catalogue", "in_domains", "outcome"),
    [
        ([product("daily_close", "close_price", "volume")], {"close_price", "volume"}, "reuse"),
        ([product("closes", "close_price"), product("volumes", "volume")], {"close_price", "volume"}, "compose"),
    ],
)
def test_checks_run_in_order(catalogue, in_domains, outcome):
    """A product that covers everything wins over data in the domains."""
    result = route(element_keys=["close_price", "volume"], terms=[], catalogue=catalogue, in_domains=in_domains)
    assert result.outcome == outcome


@pytest.mark.parametrize(
    ("rule", "catalogue", "chosen"),
    [
        # 1. the most elements covered, even with more products (a single product covers only one)
        ("most covered", [product("closes", "close_price"), product("bids", "bid_price")], ["bids", "closes"]),
        # 2. the fewest products
        ("fewest products", [product("closes", "close_price"), product("bids", "bid_price"),
                             product("z_both", "close_price", "bid_price")], ["z_both"]),
        # 3. the most products the requester is entitled to (the restricted one sorts first by code)
        ("most entitled", [product("a_restricted", "close_price", "bid_price", entitled=False),
                           product("z_open", "close_price", "bid_price")], ["z_open"]),
        # 4. the fewest elements held beyond the request
        ("fewest extra", [product("a_wide", "close_price", "bid_price", "volume"),
                          product("z_lean", "close_price", "bid_price")], ["z_lean"]),
        # 5. product codes in alphabetical order
        ("codes", [product("b_prices", "close_price", "bid_price"),
                   product("a_prices", "close_price", "bid_price")], ["a_prices"]),
    ],
)
def test_cover_rule_tie_breaks(rule, catalogue, chosen):
    result = route(element_keys=["bid_price", "close_price"], terms=[], catalogue=catalogue, in_domains=set())
    assert codes(result.products if isinstance(result, Compose) else [result.product]) == chosen, rule


def test_an_open_product_beats_a_restricted_one_covering_the_same_elements():
    for order in (1, -1):  # whatever the catalogue order
        catalogue = [product("restricted_closes", "close_price", entitled=False), product("open_closes", "close_price")]
        result = route(element_keys=["close_price"], terms=[], catalogue=catalogue[::order], in_domains=set())
        assert isinstance(result, Reuse)
        assert (result.product.code, result.product.entitled) == ("open_closes", True)


def test_at_most_three_products():
    catalogue = [product(f"{c}_only", k) for c, k in
                 (("a", "close_price"), ("b", "bid_price"), ("c", "ask_price"), ("d", "volume"))]
    result = route(element_keys=["ask_price", "bid_price", "close_price", "volume"], terms=[], catalogue=catalogue,
                   in_domains=set())
    assert isinstance(result, RaiseRequirement)
    assert codes(result.covered_by) == ["a_only", "b_only", "c_only"]
    assert result.elements == ["volume"]


def test_restricted_product_still_counts():
    result = route(element_keys=["isin"], terms=[], catalogue=[product("bond_reference", "isin", entitled=False)],
                   in_domains=set())
    assert isinstance(result, Reuse)
    assert (result.product.entitled, result.product.route) == (False, "Ask Devon Owner for access to bond_reference")


# ---- route_request: the same checks on the database


@pytest.fixture
def in_the_domains(db_session):
    """Put elements in the domains: a registered feed with a released load and current mappings."""
    from core.repositories.feeds import FeedRepository

    def _put(*keys):
        feeds = FeedRepository(db_session)
        from tests.onboarded import registered_feed

        feed = registered_feed(db_session, feeds.create_candidate(code="synthetic_feed", name="Synthetic feed").id)
        from tests.configured import a_load, approved_mapping

        for key in keys:
            approved_mapping(db_session, feed.id, key.upper(), key)
        a_load(db_session, feed.id, "released")  # 0018: loaded first, then released

    return _put


def request(db_session, element_keys, terms=(), user_id="dev-requester", roles=("requester",)):
    return route_request(db_session, element_keys=list(element_keys), terms=list(terms), user_id=user_id,
                         roles=list(roles), display_name=lambda uid: {"dev-data-owner": "Devon Owner"}.get(uid, uid))


def test_route_request_reads_products_access_and_coverage(db_session, make_product, in_the_domains):
    make_product("bond_reference", name="Bond reference", domain="security_master", visibility="restricted",
                 element_keys=["isin"])
    in_the_domains("volume")
    reuse = request(db_session, ["isin"])
    assert isinstance(reuse, Reuse)
    assert (reuse.product.entitled, reuse.product.route) == (False, "Ask Devon Owner for access to Bond reference")
    created = request(db_session, ["isin", "volume"])
    assert isinstance(created, CreateProduct)
    assert (created.elements, codes(created.covered_by)) == (["volume"], ["bond_reference"])


def test_route_request_uses_grants(db_session, make_product):
    p = make_product("bond_reference", domain="security_master", visibility="restricted", element_keys=["isin"])
    ProductGrantRepository(db_session).record(product_id=p.id, subject_type="user", subject="dev-requester",
                                              action="grant", decided_by="dev-data-owner")
    result = request(db_session, ["isin"])
    assert (result.product.entitled, result.product.route) == (True, None)
