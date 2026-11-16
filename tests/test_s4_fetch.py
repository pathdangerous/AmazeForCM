"""S4 fetch (core/steps/s4_fetch.py): read a feed's source, store the file, hand it to step 02.

Outcomes (CLAUDE.md): fetched; source_down. Phase 3 plan, assumptions 10 to 15 and changes 2 and 3:
- the file is stored under raw/<feed>/<date>/<fetch id>/<file>, so two fetches on one day keep both;
- fetched moves every requirement linked to the feed that is source_selected to onboarding;
- source_down records the reason; the requirement waits;
- the backend team is told only on a change: "File ready to profile" on the feed's first fetched, and
  "Source down" on the first failure after a success (or on a first fetch that fails);
- the connector is always closed.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text

from core.repositories.errors import FeedHasNoSource
from core.repositories.feeds import FeedRepository
from core.repositories.notifications import NotificationRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.known_source import find_known_source
from core.steps.s4_fetch import Fetched, SourceDown
from tests.step_chains import fetch_source
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


def test_a_fetch_moves_only_requirements_that_currently_select_the_feed(db_session, selected, fetch, sources,
                                                                        requirement):
    """Phase 3 plan, assumption 54: a requirement that released this feed and now waits on another source
    is not touched, even though it is source_selected again."""
    from core.repositories.source_choices import SourceChoiceRepository

    first, feed = selected
    moved_on = requirement("moved on")
    find_known_source(db_session, requirement_id=moved_on.id, catalogue=CATALOGUE)
    choices, requirements = SourceChoiceRepository(db_session), RequirementRepository(db_session)
    choices.record_release(requirement_id=moved_on.id, reason="requester_asked", decided_by="dev-requester")
    requirements.advance(moved_on.id, "awaiting_source_choice")
    other = FeedRepository(db_session).create_candidate(code="syn_other", name="Other", source_code="syn_other",
                                                        connector="local_file", source_ref="files/other.csv")
    choices.record_known(requirement_id=moved_on.id, feed_id=other.id, source_code="syn_other")
    requirements.advance(moved_on.id, "source_selected")

    put_file(sources)
    assert fetch(feed).requirements_onboarding == [first.id]
    assert (status(db_session, first), status(db_session, moved_on)) == ("onboarding", "source_selected")


# ---------------------------------------------------------------- 3b: release after N failed days (assumption 51)


def ago(days, hour=None):
    """A moment `days` ago (UTC), or that day at `hour`:00 UTC."""
    from datetime import timedelta

    moment = datetime.now(UTC) - timedelta(days=days)
    return moment if hour is None else moment.replace(hour=hour, minute=0, second=0, microsecond=0)


def the_feed(db_session):
    feeds = FeedRepository(db_session)
    return feeds.get_by_source("syn_exchange_volumes") or feeds.create_candidate(
        code="syn_exchange_volumes", name="Exchange daily volumes", source_code="syn_exchange_volumes",
        connector="local_file", source_ref="files/exchange_volumes.csv")


def selected_days_ago(db_session, requirement, days, title="volume"):
    """A requirement whose known-source selection of syn_exchange_volumes was made `days` ago. Inserting
    rows with a past created_at is allowed on an append-only table; nothing is updated in it."""
    from sqlalchemy import text

    req, feed = requirement(title), the_feed(db_session)
    db_session.execute(text(
        "INSERT INTO source_choices (requirement_id, version, action, feed_id, source_code, basis, created_at) "
        "VALUES (:r, 1, 'selected', :f, 'syn_exchange_volumes', 'known_source', :at)"),
        {"r": req.id, "f": feed.id, "at": ago(days)})
    db_session.execute(text("UPDATE requirements SET status = 'source_selected' WHERE id = :id"), {"id": req.id})
    db_session.expire_all()
    return req, feed


def past_fetch(db_session, feed, at, outcome="source_down"):
    from sqlalchemy import text

    values = {"blob_ref": "raw/x/old.csv", "size": 10, "detail": None} if outcome == "fetched" else \
        {"blob_ref": None, "size": None, "detail": "not answering"}
    db_session.execute(text(
        "INSERT INTO source_fetches (feed_id, outcome, blob_ref, size_bytes, detail, created_at) "
        "VALUES (:f, :o, :blob_ref, :size, :detail, :at)"), {"f": feed.id, "o": outcome, "at": at, **values})


def fetch_with_rule(db_session, sources, blobs, feed, n):
    return fetch_source(db_session, feed_id=feed.id, connector_factory=lambda name: LocalFileConnector(sources),
                        blob_store=blobs, notifier=InAppNotifier(db_session), backend_team=TEAM, today=DAY,
                        release_after_days=n, catalogue=CATALOGUE)


def test_both_loops_lock_the_requirements_in_id_order(db_session, requirement, sources, blobs, monkeypatch):
    """CLAUDE.md's lock order: the feed row, then the requirements in id order (independent review, finding
    7). The requirement selected first is given the larger id, so selection order is not id order."""
    from sqlalchemy import text

    feed, reqs = the_feed(db_session), sorted((requirement("volume a"), requirement("volume b")), key=lambda r: r.id)
    for req, days in ((reqs[1], 5), (reqs[0], 3)):  # the larger id selected first
        db_session.execute(text(
            "INSERT INTO source_choices (requirement_id, version, action, feed_id, source_code, basis, created_at) "
            "VALUES (:r, 1, 'selected', :f, 'syn_exchange_volumes', 'known_source', :at)"),
            {"r": req.id, "f": feed.id, "at": ago(days)})
        db_session.execute(text("UPDATE requirements SET status = 'source_selected' WHERE id = :id"), {"id": req.id})
    db_session.expire_all()
    locked, original = [], RequirementRepository.lock

    def spy(self, requirement_id):
        locked.append(requirement_id)
        return original(self, requirement_id)

    monkeypatch.setattr(RequirementRepository, "lock", spy)
    remove_file(sources)
    fetch_with_rule(db_session, sources, blobs, feed, 30)  # source_down: the N-day loop (nobody is released)
    put_file(sources)
    fetch_with_rule(db_session, sources, blobs, feed, 30)  # fetched: the onboarding loop
    ids = [r.id for r in reqs]
    assert locked == ids + ids


def test_released_after_n_failed_days_in_a_row(db_session, requirement, sources, blobs):
    req, feed = selected_days_ago(db_session, requirement, 10)
    past_fetch(db_session, feed, ago(2))
    result = fetch_with_rule(db_session, sources, blobs, feed, 3)  # day 2 of 3: waits
    assert isinstance(result, SourceDown) and result.released == []
    past_fetch(db_session, feed, ago(1))  # the history now reads: 2 days ago, 1 day ago, today (twice)
    result = fetch_with_rule(db_session, sources, blobs, feed, 3)
    assert result.released == [req.id]
    assert status(db_session, req) == "no_source_found"  # a known source: nothing else is listed
    from core.repositories.source_choices import SourceChoiceRepository

    choice = SourceChoiceRepository(db_session).current(req.id)
    assert (choice.action, choice.reason, choice.decided_by) == ("released", "source_down_n_days", None)
    assert "Source released: syn_exchange_volumes" in inbox(db_session)


def test_two_failures_on_one_day_count_as_one_day(db_session, requirement, sources, blobs):
    req, feed = selected_days_ago(db_session, requirement, 10)
    past_fetch(db_session, feed, ago(1, hour=1))
    past_fetch(db_session, feed, ago(1, hour=2))  # yesterday, twice: one day
    result = fetch_with_rule(db_session, sources, blobs, feed, 3)  # yesterday and today: 2 of 3
    assert result.released == [] and status(db_session, req) == "source_selected"


def test_a_fetched_day_starts_the_count_again(db_session, requirement, sources, blobs):
    req, feed = selected_days_ago(db_session, requirement, 10)
    for days, outcome in ((4, "source_down"), (3, "source_down"), (2, "fetched"), (1, "source_down")):
        past_fetch(db_session, feed, ago(days), outcome)
    result = fetch_with_rule(db_session, sources, blobs, feed, 3)  # since the fetched: 1 day ago and today
    assert result.released == [] and status(db_session, req) == "source_selected"


def test_days_before_the_selection_do_not_count(db_session, requirement, sources, blobs):
    """A requirement that selects a source that is already down gets its own N days."""
    feed = the_feed(db_session)
    for days in (4, 3, 2):
        past_fetch(db_session, feed, ago(days))
    req, feed = selected_days_ago(db_session, requirement, 1.5)
    result = fetch_with_rule(db_session, sources, blobs, feed, 2)  # since the selection: today only
    assert result.released == [] and status(db_session, req) == "source_selected"


def test_n_is_the_setting(db_session, requirement, sources, blobs):
    from apps.api.jobs import DailyFetch
    from ports.config import Settings
    from ports.identity.dev import DevIdentity

    daily = DailyFetch.from_settings(Settings(_env_file=None, source_down_release_days=5), DevIdentity())
    assert daily.release_after_days == 5
    assert DailyFetch.from_settings(Settings(_env_file=None), DevIdentity()).release_after_days == 3
    req, feed = selected_days_ago(db_session, requirement, 10)
    assert fetch_with_rule(db_session, sources, blobs, feed, 1).released == [req.id]  # N=1: the first failed day


def test_an_onboarding_requirement_is_never_released(db_session, selected, sources, blobs):
    """Its file is already with step 02; a feed that stops answering later is a late delivery (phase 7)."""
    req, feed = selected
    put_file(sources)
    fetch_with_rule(db_session, sources, blobs, feed, 1)
    remove_file(sources)
    assert fetch_with_rule(db_session, sources, blobs, feed, 1).released == []
    assert status(db_session, req) == "onboarding"


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
