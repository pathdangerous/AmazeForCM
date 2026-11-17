"""DomainCoverage: which glossary elements are already "in the domains" (phase 2 plan, assumption 27).

An element is in the domains when some feed is registered, has at least one released load, and its
current mapping (the highest version of a source field) points at the element. Routing uses this for
the create_product outcome. Nothing reaches this state through the application until phase 7, so the
tests build it with approved proposals, as phase 1 did.
"""

import pytest
from sqlalchemy import text

from core.repositories.configuration import MappingRepository
from core.repositories.coverage import DomainCoverage
from core.repositories.feeds import FeedRepository

ASKED = ["close_price", "open_price", "volume"]


@pytest.fixture
def coverage(db_session, glossary):
    return DomainCoverage(db_session)


@pytest.fixture
def feed(db_session, glossary):
    """Create a feed; registered by default."""

    def _feed(code="synthetic_prices", *, registered=True):
        feeds = FeedRepository(db_session)
        candidate = feeds.create_candidate(code=code, name=code.replace("_", " ").capitalize())
        from tests.onboarded import registered_feed

        return registered_feed(db_session, candidate.id) if registered else candidate

    return _feed


@pytest.fixture
def map_field(db_session):
    """Add the next mapping version of a source field, from its own approved proposal (tests/configured.py)."""
    from tests.configured import approved_mapping

    def _map(feed, source_field, element_key):
        return approved_mapping(db_session, feed.id, source_field, element_key)

    return _map


@pytest.fixture
def load(db_session):
    def _load(feed, status="released"):
        from tests.configured import a_load

        return a_load(db_session, feed.id, status)  # 0018: each status is reached the way the database allows

    return _load


def test_registered_feed_with_a_released_load_and_a_current_mapping(coverage, feed, map_field, load):
    prices = feed()
    map_field(prices, "ClosePx", "close_price")
    map_field(prices, "Vol", "volume")
    load(prices)
    assert coverage.in_domains(ASKED) == {"close_price", "volume"}
    assert coverage.in_domains(["open_price", "isin"]) == set()  # only what was asked, only what is mapped
    assert coverage.in_domains([]) == set()


def test_a_feed_that_is_not_registered_does_not_count(coverage, feed, db_session, approved_proposal):
    """From 0016 a candidate feed cannot be mapped at all (FeedNotRegistered); a released load written by
    hand still does not make it count: the stage check holds on its own."""
    from core.repositories.errors import FeedNotRegistered

    candidate = feed(registered=False)
    proposal = approved_proposal(kind="mapping", feed_id=candidate.id, payload={
        "source_field": "ClosePx", "column_type": None, "candidates": ["close_price"],
        "recommended": "close_price", "reason": "Synthetic."})
    with pytest.raises(FeedNotRegistered):
        MappingRepository(db_session).add_version(feed_id=candidate.id, source_field="ClosePx",
                                                  element_key="close_price", proposal_id=proposal.id)
    # 0018: a load of any status can only be written by hand with the status received; the stage check holds on
    # its own, so this candidate feed has a received load and still does not count
    db_session.execute(
        text("INSERT INTO loads (feed_id, file_ref, purpose) VALUES (:f, 'local://raw/x.csv', 'go_live_test')"),
        {"f": candidate.id},
    )
    assert coverage.in_domains(ASKED) == set()


@pytest.mark.parametrize("statuses", [(), ("received",), ("held", "failed_rolled_back")])
def test_a_feed_without_a_released_load_does_not_count(coverage, feed, map_field, load, statuses):
    prices = feed()
    map_field(prices, "ClosePx", "close_price")
    for status in statuses:
        load(prices, status)
    assert coverage.in_domains(ASKED) == set()


def test_one_released_load_among_others_is_enough(coverage, feed, map_field, load):
    prices = feed()
    map_field(prices, "ClosePx", "close_price")
    load(prices, "held")
    load(prices, "released")
    load(prices, "failed_rolled_back")
    assert coverage.in_domains(ASKED) == {"close_price"}


def test_a_superseded_mapping_does_not_count(coverage, feed, map_field, load):
    """Version 2 of the source field points at another element: only the current mapping counts."""
    prices = feed()
    map_field(prices, "Px", "close_price")
    map_field(prices, "Px", "open_price")
    load(prices)
    assert coverage.in_domains(ASKED) == {"open_price"}


def test_any_one_feed_is_enough(coverage, feed, map_field, load):
    without_load = feed("prices_without_load")
    map_field(without_load, "ClosePx", "close_price")
    map_field(without_load, "Vol", "volume")
    released = feed("prices_released")
    map_field(released, "Close", "close_price")
    load(released)
    assert coverage.in_domains(ASKED) == {"close_price"}
