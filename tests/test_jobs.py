"""The daily fetch job (apps/api/jobs.py): carried item 3, scheduler jobs survive an API restart.

Phase 3 plan, assumptions 17 and 18: one daily job, registered at every start-up, that reads the feeds
table when it runs. Nothing per feed is registered, so nothing is lost on a restart. Each feed is
fetched in its own transaction; one feed's failure does not stop the others.
"""

from contextlib import nullcontext

import pytest

from apps.api.jobs import DAILY_FETCH_HOUR, DAILY_FETCH_JOB_ID, DailyFetch
from core.repositories.requirements import RequirementRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.known_source import find_known_source
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from ports.connectors.local_file import LocalFileConnector
from ports.storage.local import LocalBlobStore
from tests.conftest import running_app
from tests.onboarded import registered_feed

pytestmark = pytest.mark.anyio

CATALOGUE = SourceCatalogue.load(DEFAULT_CATALOGUE)
CSV = b"instr_id,trade_date,traded_qty\nsyn-0001,2026-11-02,120500\n"


@pytest.fixture
def sources(tmp_path):
    (tmp_path / "sources" / "files").mkdir(parents=True)
    (tmp_path / "sources" / "files" / "exchange_volumes.csv").write_bytes(CSV)
    return tmp_path / "sources"


@pytest.fixture
def daily(db_session, sources, tmp_path):
    """The job's dependencies, pointed at the test's session, a temporary blob store and sources."""

    def _daily(connector_factory=None):
        return DailyFetch(
            session_factory=lambda: nullcontext(db_session),
            blob_store=LocalBlobStore(tmp_path / "blob"),
            connector_factory=connector_factory or (lambda name: LocalFileConnector(sources)),
            backend_team=lambda: ["dev-backend-team"],
        )

    return _daily


@pytest.fixture
def selected(db_session, glossary):
    """A requirement handed to syn_exchange_volumes, committed to the test's savepoint (the job commits)."""

    def _select(title="volume"):
        req = RequirementRepository(db_session).create(requester_id="dev-requester", title=title,
                                                       element_keys=["volume"])
        feed = find_known_source(db_session, requirement_id=req.id, catalogue=CATALOGUE).feed
        db_session.commit()
        return req, feed

    return _select


def test_the_job_closes_a_list_whose_sources_left_the_catalogue(db_session, daily, glossary, make_proposal):
    """Independent review, fix 7: the daily job loads the catalogue on each run; a requirement waiting
    on listed sources of which none is left in it becomes no_source_found."""
    import dataclasses

    from sqlalchemy import text

    from core.repositories.requirements import RequirementRepository

    def waiting(title, source_code):
        req = RequirementRepository(db_session).create(requester_id="dev-requester", title=title,
                                                       element_keys=["open_price"])
        make_proposal(kind="source_list", roles=("requester",), payload={"source_code": source_code},
                      requirement_id=req.id)
        db_session.execute(text("UPDATE requirements SET status = 'awaiting_source_choice' WHERE id = :id"),
                           {"id": req.id})
        return req

    gone, kept = waiting("open price", "syn_vendor_eod_prices"), waiting("open price too", "syn_backup_eod_prices")
    db_session.commit()
    without_vendor = SourceCatalogue([e for e in CATALOGUE.entries if e.code != "syn_vendor_eod_prices"])
    dataclasses.replace(daily(), catalogue_loader=lambda: without_vendor).run()
    statuses = {r.id: r.status for r in RequirementRepository(db_session).list()}
    assert (statuses[gone.id], statuses[kept.id]) == ("no_source_found", "awaiting_source_choice")


async def start_and_stop(daily_fetch):
    """One API start-up and shutdown with this job; returns the jobs the scheduler held while running."""
    from apps.api.main import app

    app.state.daily_fetch = daily_fetch
    try:
        async with running_app():
            return app.state.scheduler.list_jobs(), app.state.scheduler
    finally:
        del app.state.daily_fetch


async def test_the_daily_job_is_registered_at_every_start(daily):
    for _ in range(2):
        jobs, _ = await start_and_stop(daily())
        assert [(j.job_id, j.hour, j.minute) for j in jobs] == [(DAILY_FETCH_JOB_ID, DAILY_FETCH_HOUR, 0)]


async def test_after_a_restart_the_job_fetches_feeds_read_from_the_database(db_session, daily, selected):
    """The feed is created while the API is down. The new start-up registers the job again, and the job
    finds the feed in the database: nothing about the feed was kept in the scheduler."""
    from apps.api.main import app

    await start_and_stop(daily())
    req, feed = selected()
    app.state.daily_fetch = daily()
    try:
        async with running_app():
            app.state.scheduler.run_now(DAILY_FETCH_JOB_ID)
    finally:
        del app.state.daily_fetch
    assert SourceFetchRepository(db_session).latest(feed.id).outcome == "fetched"
    assert RequirementRepository(db_session).get(req.id).status == "onboarding"


def test_the_job_fetches_every_feed_with_a_source(db_session, daily, selected):
    _, feed = selected()
    assert daily().run() == {"syn_exchange_volumes": "fetched"}
    assert len(SourceFetchRepository(db_session).list_for_feed(feed.id)) == 1


def test_one_failing_feed_does_not_stop_the_others(db_session, daily, selected, sources):
    """An unexpected error in one feed is rolled back and reported; the next feed is still fetched."""
    from core.repositories.feeds import FeedRepository

    feeds = FeedRepository(db_session)
    broken = feeds.create_candidate(
        code="syn_broken", name="Broken", source_code="syn_broken", connector="http",
        source_ref="http://127.0.0.1:9/x.csv",
    )
    registered_feed(db_session, broken.id)  # 3b, Q4: registered, so fetched with no selection
    _, feed = selected()

    def factory(name):
        if name == "http":
            raise RuntimeError("connector cannot be built")
        return LocalFileConnector(sources)

    assert daily(factory).run() == {"syn_broken": "error: connector cannot be built", "syn_exchange_volumes": "fetched"}
    assert SourceFetchRepository(db_session).latest(feed.id).outcome == "fetched"


async def test_a_stub_scheduler_gets_no_job(daily, monkeypatch):
    from apps.api import main
    from ports.scheduler.azure_functions import AzureFunctionsScheduler

    monkeypatch.setattr(main, "get_scheduler", lambda settings: AzureFunctionsScheduler())
    jobs_seen = []
    main.app.state.daily_fetch = daily()
    try:
        async with running_app():
            jobs_seen.append(main.app.state.scheduler)
    finally:
        del main.app.state.daily_fetch
    assert getattr(jobs_seen[0], "is_stub", False)


# ---------------------------------------------------------------- review fix 4: the catalogue is read on each run


def test_the_job_reads_the_catalogue_on_each_run(daily):
    loads = []

    def load():
        loads.append(1)
        return CATALOGUE

    job = daily()
    job.catalogue_loader = load
    job.run()
    job.run()
    assert len(loads) == 2


def test_building_the_job_does_not_read_the_catalogue(tmp_path):
    """At start-up nothing is read, so an edited catalogue applies from the next run."""
    from ports.config import Settings
    from ports.identity.dev import DevIdentity

    missing = tmp_path / "no_catalogue_yet.json"
    job = DailyFetch.from_settings(Settings(_env_file=None, source_catalogue=missing), DevIdentity())
    with pytest.raises(FileNotFoundError):
        job.catalogue_loader()


# ---------------------------------------------------------------- 3b: which feeds the job fetches (Q4, assumption 54)


def test_the_job_always_fetches_a_registered_feed(db_session, daily):
    """A registered feed is a regular feed: fetched though no requirement selects it."""
    from core.repositories.feeds import FeedRepository

    feeds = FeedRepository(db_session)
    feed = feeds.create_candidate(code="syn_exchange_volumes", name="Exchange daily volumes",
                                  source_code="syn_exchange_volumes", connector="local_file",
                                  source_ref="files/exchange_volumes.csv")
    registered_feed(db_session, feed.id)
    db_session.commit()  # to the test's savepoint: the job ends its read transaction before the fetch
    assert daily().run() == {"syn_exchange_volumes": "fetched"}


def test_the_job_skips_a_candidate_feed_that_no_requirement_selects(db_session, daily, selected):
    """Every requirement released it: the candidate feed is idle, so no more failures or notifications."""
    from core.repositories.source_choices import SourceChoiceRepository

    req, feed = selected()
    SourceChoiceRepository(db_session).record_release(requirement_id=req.id, reason="requester_asked",
                                                      decided_by="dev-requester")
    RequirementRepository(db_session).advance(req.id, "no_source_found")
    assert daily().run() == {}
    assert SourceFetchRepository(db_session).list_for_feed(feed.id) == []


def test_a_new_selection_wakes_the_feed(db_session, daily, selected):
    from core.repositories.source_choices import SourceChoiceRepository

    first, feed = selected("first")
    SourceChoiceRepository(db_session).record_release(requirement_id=first.id, reason="requester_asked",
                                                      decided_by="dev-requester")
    RequirementRepository(db_session).advance(first.id, "no_source_found")
    assert daily().run() == {}
    second, same_feed = selected("second")
    assert same_feed.id == feed.id
    assert daily().run() == {"syn_exchange_volumes": "fetched"}
    assert RequirementRepository(db_session).get(second.id).status == "onboarding"
    assert RequirementRepository(db_session).get(first.id).status == "no_source_found"
