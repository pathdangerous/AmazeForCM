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
from core.repositories.loads import LoadRepository

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
        return feeds.register(candidate.id, owner_id="dev-data-owner") if registered else candidate

    return _feed


@pytest.fixture
def map_field(db_session, approved_proposal):
    """Add the next mapping version of a source field, from its own approved proposal."""

    def _map(feed, source_field, element_key):
        return MappingRepository(db_session).add_version(
            feed_id=feed.id, source_field=source_field, element_key=element_key,
            proposal_id=approved_proposal(kind="mapping").id,
        )

    return _map


@pytest.fixture
def load(db_session):
    def _load(feed, status="released"):
        loads = LoadRepository(db_session)
        created = loads.create(feed_id=feed.id, file_ref=f"local://raw/{feed.code}/2026-01-02.csv")
        return created if status == "received" else loads.set_status(created.id, status)

    return _load


def test_registered_feed_with_a_released_load_and_a_current_mapping(coverage, feed, map_field, load):
    prices = feed()
    map_field(prices, "ClosePx", "close_price")
    map_field(prices, "Vol", "volume")
    load(prices)
    assert coverage.in_domains(ASKED) == {"close_price", "volume"}
    assert coverage.in_domains(["open_price", "isin"]) == set()  # only what was asked, only what is mapped
    assert coverage.in_domains([]) == set()


def test_a_feed_that_is_not_registered_does_not_count(coverage, feed, map_field, db_session):
    candidate = feed(registered=False)
    map_field(candidate, "ClosePx", "close_price")
    # The repository gives loads only to registered feeds, so the released load is written by hand:
    # the stage check must hold on its own.
    db_session.execute(
        text("INSERT INTO loads (feed_id, file_ref, status) VALUES (:f, 'local://raw/x.csv', 'released')"),
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
