"""S4 fetch (core/steps/s4_fetch.py): read a feed's source, store the file, hand it to step 02.

Outcomes (CLAUDE.md): fetched; source_down. Phase 3 plan, assumptions 10 to 15 and changes 2 and 3:
- the file is stored under raw/<feed>/<date>/<fetch id>/<file>, so two fetches on one day keep both;
- fetched moves every requirement linked to the feed that is source_selected to onboarding;
- source_down records the reason; the requirement waits;
- the backend team is told only on a change: "File ready to profile" on the feed's first fetched, and
  "Source down" on the first failure after a success (or on a first fetch that fails);
- the connector is always closed.
"""

from datetime import date

import pytest
from sqlalchemy import text

from core.repositories.errors import FeedHasNoSource
from core.repositories.feeds import FeedRepository
from core.repositories.notifications import NotificationRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.known_source import find_known_source
from core.steps.s4_fetch import Fetched, SourceDown, fetch_source
from ports.connectors.base import FetchedFile, SourceUnavailable
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from ports.connectors.local_file import LocalFileConnector
from ports.notify.inapp import InAppNotifier
from ports.storage.local import LocalBlobStore

CATALOGUE = SourceCatalogue.load(DEFAULT_CATALOGUE)
CSV = b"instr_id,trade_date,traded_qty\nsyn-0001,2026-11-02,120500\n"
DAY = date(2026, 11, 14)
TEAM = ["dev-backend-team"]


@pytest.fixture
def sources(tmp_path):
    (tmp_path / "sources" / "files").mkdir(parents=True)
    return tmp_path / "sources"


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blob")


@pytest.fixture
def requirement(db_session, glossary):
    def _make(title="volume"):
        return RequirementRepository(db_session).create(requester_id="dev-requester", title=title,
                                                        element_keys=["volume"])

    return _make


@pytest.fixture
def selected(db_session, requirement):
    """A requirement handed to the known source syn_exchange_volumes: its candidate feed."""
    req = requirement()
    found = find_known_source(db_session, requirement_id=req.id, catalogue=CATALOGUE)
    return req, found.feed


@pytest.fixture
def fetch(db_session, sources, blobs):
    def _fetch(feed, *, day=DAY, factory=None):
        return fetch_source(
            db_session, feed_id=feed.id, connector_factory=factory or (lambda name: LocalFileConnector(sources)),
            blob_store=blobs, notifier=InAppNotifier(db_session), backend_team=TEAM, today=day,
        )

    return _fetch


def put_file(sources, content=CSV):
    (sources / "files" / "exchange_volumes.csv").write_bytes(content)


def remove_file(sources):
    (sources / "files" / "exchange_volumes.csv").unlink(missing_ok=True)


def inbox(db_session):
    return [n.subject for n in reversed(NotificationRepository(db_session).list_for("dev-backend-team"))]


def status(db_session, req):
    return RequirementRepository(db_session).get(req.id).status


def test_fetched_stores_the_file_and_moves_the_requirement_to_onboarding(db_session, selected, fetch, sources,
                                                                          blobs):
    req, feed = selected
    put_file(sources)
    result = fetch(feed)
    assert isinstance(result, Fetched)
    assert result.blob_ref == f"raw/syn_exchange_volumes/2026-11-14/{result.fetch_id}/exchange_volumes.csv"
    assert blobs.get(result.blob_ref) == CSV
    assert (result.size_bytes, result.requirements_onboarding) == (len(CSV), [req.id])
    row = SourceFetchRepository(db_session).latest_fetched(feed.id)
    assert (row.id, row.blob_ref, row.size_bytes) == (result.fetch_id, result.blob_ref, len(CSV))
    assert status(db_session, req) == "onboarding"


def test_source_down_is_recorded_and_the_requirement_waits(db_session, selected, fetch):
    req, feed = selected
    result = fetch(feed)  # no file
    assert isinstance(result, SourceDown)
    assert "exchange_volumes.csv" in result.detail
    row = SourceFetchRepository(db_session).latest(feed.id)
    assert (row.outcome, row.blob_ref, row.detail) == ("source_down", None, result.detail)
    assert status(db_session, req) == "source_selected"


def test_an_empty_file_is_source_down(db_session, selected, fetch, sources):
    req, feed = selected
    put_file(sources, b"")
    result = fetch(feed)
    assert isinstance(result, SourceDown) and "empty" in result.detail
    assert status(db_session, req) == "source_selected"


def test_two_fetches_on_one_day_keep_both_files(db_session, selected, fetch, sources, blobs):
    _, feed = selected
    put_file(sources, CSV)
    first = fetch(feed)
    put_file(sources, CSV + b"syn-0002,2026-11-02,98200\n")
    second = fetch(feed)
    assert first.blob_ref != second.blob_ref
    assert blobs.get(first.blob_ref) == CSV
    assert blobs.get(second.blob_ref).endswith(b"98200\n")
    assert blobs.list_keys("raw/syn_exchange_volumes/2026-11-14/") == sorted([first.blob_ref, second.blob_ref])


def test_file_ready_is_sent_on_the_first_fetched_only(db_session, selected, fetch, sources):
    _, feed = selected
    put_file(sources)
    fetch(feed)
    fetch(feed, day=date(2026, 11, 15))
    assert inbox(db_session) == ["File ready to profile: syn_exchange_volumes"]


def test_source_down_is_sent_once_per_run_of_failures(db_session, selected, fetch, sources):
    """down, down, fetched, down: the first fetch failing counts as a change; the second failure in a
    row does not; the failure after a success does."""
    _, feed = selected
    fetch(feed)
    fetch(feed)
    put_file(sources)
    fetch(feed)
    remove_file(sources)
    fetch(feed)
    assert inbox(db_session) == [
        "Source down: syn_exchange_volumes",
        "File ready to profile: syn_exchange_volumes",
        "Source down: syn_exchange_volumes",
    ]


def test_only_requirements_waiting_on_the_feed_move(db_session, selected, fetch, sources, requirement):
    """A requirement that is already onboarding is not moved again; one selected later moves on the next
    fetch."""
    first, feed = selected
    put_file(sources)
    assert fetch(feed).requirements_onboarding == [first.id]
    later = requirement("later")
    find_known_source(db_session, requirement_id=later.id, catalogue=CATALOGUE)
    assert fetch(feed).requirements_onboarding == [later.id]
    assert (status(db_session, first), status(db_session, later)) == ("onboarding", "onboarding")


class SpyConnector:
    name = "spy"

    def __init__(self, result):
        self.result, self.closed = result, False

    def fetch(self, source_ref):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


@pytest.mark.parametrize("result", [SourceUnavailable("not answering"),
                                    FetchedFile(source_ref="r", filename="x.csv", content=CSV, content_type=None)])
def test_the_step_closes_the_connector_even_when_the_source_is_down(selected, fetch, result):
    _, feed = selected
    spy = SpyConnector(result)
    fetch(feed, factory=lambda name: spy)
    assert spy.closed


def test_a_file_name_that_is_not_a_safe_key_segment_is_cleaned(selected, fetch, blobs):
    _, feed = selected
    spy = SpyConnector(FetchedFile(source_ref="r", filename=".hidden:prices?.csv", content=CSV, content_type=None))
    result = fetch(feed, factory=lambda name: spy)
    assert result.blob_ref.endswith(f"/{result.fetch_id}/hidden_prices_.csv")
    assert blobs.get(result.blob_ref) == CSV


def test_a_feed_without_a_source_cannot_be_fetched(db_session, fetch):
    feed = FeedRepository(db_session).create_candidate(code="hand_made", name="Hand made")
    with pytest.raises(FeedHasNoSource, match="hand_made"):
        fetch(feed)


def test_nothing_is_written_to_the_feed_itself(db_session, selected, fetch, sources):
    """The feed stays a candidate; the hand-off is the fetch row, the file and the choice."""
    _, feed = selected
    put_file(sources)
    fetch(feed)
    stage = db_session.execute(text("SELECT stage FROM feeds WHERE id = :id"), {"id": feed.id}).scalar_one()
    assert stage == "candidate"
