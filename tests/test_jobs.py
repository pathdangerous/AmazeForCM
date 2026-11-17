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


def test_the_job_closes_a_list_whose_only_source_was_rejected(db_session, daily, glossary, make_proposal):
    """Phase 4 plan, assumption 25: a requirement that waits on a list whose only source the backend team
    rejected is closed by the daily job, and the backend team is told it was rejected, not that it left
    the catalogue (independent review, finding 5)."""
    import dataclasses

    from sqlalchemy import text

    from core.repositories.feeds import FeedRepository
    from core.repositories.notifications import NotificationRepository
    from tests.onboarded import rejected_feed

    req = RequirementRepository(db_session).create(requester_id="dev-requester", title="open price",
                                                   element_keys=["open_price"])
    make_proposal(kind="source_list", roles=("requester",), payload={"source_code": "syn_vendor_eod_prices"},
                  requirement_id=req.id)
    db_session.execute(text("UPDATE requirements SET status = 'awaiting_source_choice' WHERE id = :id"), {"id": req.id})
    vendor = FeedRepository(db_session).create_candidate(
        code="syn_vendor_eod_prices", name="Vendor end-of-day prices", source_code="syn_vendor_eod_prices",
        connector="local_file", source_ref="files/vendor_eod_prices.csv")
    rejected_feed(db_session, vendor.id)  # rejected for another requirement
    db_session.commit()
    dataclasses.replace(daily(), catalogue_loader=lambda: CATALOGUE).run()
    assert RequirementRepository(db_session).get(req.id).status == "no_source_found"
    [told] = [n for n in NotificationRepository(db_session).list_for("dev-backend-team")
              if n.subject == "No source left: open price"]
    assert told.body == (f"Requirement {req.id} (open price): every listed source was rejected by the backend "
                         "team. It is now no_source_found; the requester can ask for a new scope.")


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


# ---------------------------------------------------------------- phase 4: registered feeds and a broken catalogue


def _registered_by_step_04(db_session, selected):
    """volume onboarding on syn_exchange_volumes, signed off and registered by step 04; then the requirement
    is released, so no requirement selects the feed. Committed to the test's savepoint."""
    from core.repositories.source_choices import SourceChoiceRepository
    from core.steps.s04_register import register_feed
    from ports.identity.dev import DevIdentity
    from ports.notify.inapp import InAppNotifier
    from tests.conftest import dev_user_for
    from tests.onboarded import signed_off

    req, feed = selected()
    RequirementRepository(db_session).advance(req.id, "onboarding")
    signed_off(db_session, feed.id)
    register_feed(db_session, feed_id=feed.id, user=dev_user_for("backend_team"), owner_id="dev-data-owner",
                  visibility="open", catalogue=CATALOGUE, identity=DevIdentity(), notifier=InAppNotifier(db_session))
    # The requirement releases the feed (written through the repository; no step releases an onboarding
    # requirement on request), so only its registration keeps the feed fetched.
    SourceChoiceRepository(db_session).record_release(requirement_id=req.id, reason="requester_asked",
                                                      decided_by="dev-requester")
    db_session.commit()
    return req, feed


def test_a_feed_registered_by_step_04_is_fetched_with_no_selection(db_session, daily, selected):
    """Note 4a: a registered feed is a regular feed; registration ends its dependence on a selection."""
    from core.repositories.feeds import FeedRepository

    _, feed = _registered_by_step_04(db_session, selected)
    assert FeedRepository(db_session).get(feed.id).stage == "registered"
    assert [f.id for f in FeedRepository(db_session).list_to_fetch()] == [feed.id]
    before = len(SourceFetchRepository(db_session).list_for_feed(feed.id))
    assert daily().run() == {"syn_exchange_volumes": "fetched"}
    assert len(SourceFetchRepository(db_session).list_for_feed(feed.id)) == before + 1


def _broken(job):
    import dataclasses

    def fail():
        raise ValueError("source code 'vendor_x' must start with 'syn_'")

    return dataclasses.replace(job, catalogue_loader=fail)


def test_a_catalogue_that_fails_to_load_still_fetches_registered_feeds(db_session, daily, selected):
    """Note 4d: a bad catalogue file no longer stops every fetch."""
    _registered_by_step_04(db_session, selected)
    assert _broken(daily()).run() == {"syn_exchange_volumes": "fetched"}


def test_a_catalogue_that_fails_to_load_skips_candidates_and_tells_the_backend_team(db_session, daily, selected):
    from core.repositories.notifications import NotificationRepository

    _, candidate = selected()  # a candidate feed a requirement selects: fetched only with a good catalogue
    job = _broken(daily())
    assert job.run() == {}
    assert SourceFetchRepository(db_session).list_for_feed(candidate.id) == []
    job.run()
    told = [n for n in NotificationRepository(db_session).list_for("dev-backend-team")
            if n.subject == "Source catalogue could not be read"]
    assert len(told) == 2  # once per run, until it is fixed (risk 12)
    assert "must start with 'syn_'" in told[0].body and "Registered feeds were fetched" in told[0].body


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


# ---------------------------------------------------------------- phase 5: the glossary runs worker (assumptions 10 to 12, 48)


@pytest.fixture
def glossary_runs(db_session, tmp_path):
    """The worker, pointed at the test's session (each run's commits are savepoint releases)."""
    from apps.api.jobs import GlossaryRuns
    from agents.glossary_builder import GlossaryBuilderAgent
    from ports.llm.mock import MockProvider

    def _runs(provider=None):
        return GlossaryRuns(
            session_factory=lambda: nullcontext(db_session),
            agent_factory=lambda: GlossaryBuilderAgent(provider=provider or MockProvider(),
                                                       blob_store=LocalBlobStore(tmp_path / "blob")),
            owners=lambda: ["dev-glossary-owner"], lease_seconds=300)

    return _runs


@pytest.fixture
def two_queued(db_session, glossary):
    from core.repositories.feeds import FeedRepository
    from core.steps.s05_glossary import queue_run
    from tests.test_s05_glossary import BOND_COLUMNS, BOND_SPEC

    feeds = FeedRepository(db_session)
    bond = registered_feed(db_session, feeds.create_candidate(code="syn_bond_terms", name="Bond terms").id,
                           spec=BOND_SPEC, columns=BOND_COLUMNS)
    plain = registered_feed(db_session, feeds.create_candidate(code="syn_plain", name="Plain").id)
    runs = [queue_run(db_session, feed_id=f.id, reason="registered", requested_by="dev-backend-team")[0]
            for f in (bond, plain)]
    db_session.commit()
    return runs


def test_drain_runs_every_waiting_run_each_in_its_own_transaction(db_session, glossary_runs, two_queued):
    from core.repositories.glossary_runs import GlossaryRunRepository

    assert glossary_runs().drain() == {two_queued[0].id: "done", two_queued[1].id: "done"}
    runs = GlossaryRunRepository(db_session)
    assert [runs.get(r.id).status for r in two_queued] == ["done", "done"]
    assert runs.get(two_queued[0].id).results["rating"] == "proposed"
    assert glossary_runs().drain() == {}  # nothing waits


def test_a_model_that_does_not_answer_fails_the_run_and_keeps_its_reason(db_session, glossary_runs, two_queued):
    from core.repositories.glossary_runs import GlossaryRunRepository
    from ports.llm.base import LLMUnavailable
    from tests.stand_in_agents import FixedProvider

    result = glossary_runs(FixedProvider(error=LLMUnavailable("the model did not answer"))).drain()
    bond, plain = (GlossaryRunRepository(db_session).get(r.id) for r in two_queued)
    assert result == {bond.id: "failed", plain.id: "done"}  # the plain feed asks nothing: no call
    assert (bond.status, bond.error) == ("failed", "LLMUnavailable: the model did not answer")


class RecordingScheduler:
    """Runs an immediate run_soon in the caller and only records a delayed one."""

    def __init__(self):
        self.calls = []

    def run_soon(self, job_id, func, *, delay_seconds=0.0):
        self.calls.append((job_id, delay_seconds))
        if not delay_seconds:
            func()


def test_a_kick_that_leaves_a_live_lease_schedules_one_more_just_after_it(db_session, glossary_runs, two_queued):
    """Review finding 2: the first run's worker stopped with its lease live; the kick runs the other run,
    then asks for one more kick a moment after that lease."""
    from apps.api.jobs import GLOSSARY_RUNS_JOB_ID, LEASE_GRACE_SECONDS, kick_glossary_runs
    from core.repositories.glossary_runs import GlossaryRunRepository

    runs = GlossaryRunRepository(db_session)
    assert runs.claim(lease_seconds=120).id == two_queued[0].id  # its worker stopped here
    db_session.commit()
    scheduler = RecordingScheduler()
    kick_glossary_runs(scheduler, glossary_runs())
    assert runs.get(two_queued[1].id).status == "done" and runs.get(two_queued[0].id).status == "running"
    [(first, at_once), (later, delay)] = scheduler.calls
    assert (first, at_once, later) == (GLOSSARY_RUNS_JOB_ID, 0.0, GLOSSARY_RUNS_JOB_ID)
    assert 120 - 5 + LEASE_GRACE_SECONDS < delay <= 120 + LEASE_GRACE_SECONDS


def test_a_kick_that_leaves_no_live_lease_schedules_nothing(db_session, glossary_runs, two_queued):
    from apps.api.jobs import GLOSSARY_RUNS_JOB_ID, kick_glossary_runs

    scheduler = RecordingScheduler()
    kick_glossary_runs(scheduler, glossary_runs())
    assert scheduler.calls == [(GLOSSARY_RUNS_JOB_ID, 0.0)]


# ---------------------------------------------------------------- phase 6: the job loads live feeds (task 10)


@pytest.fixture
def live(db_session, glossary):
    """A registered feed reading syn_exchange_volumes, three mappings, an approved go-live (hypercare 10 days)."""
    from core.repositories.feeds import FeedRepository
    from tests.configured import approved_mapping, go_live

    def _live(code="syn_exchange_volumes", hypercare_days=10, mapped=True):
        feed = FeedRepository(db_session).create_candidate(
            code=code, name="Exchange daily volumes", source_code=code, connector="local_file",
            source_ref="files/exchange_volumes.csv")
        registered_feed(db_session, feed.id)
        if mapped:
            for field, key in (("instr_id", "instrument_id"), ("trade_date", "price_date"),
                               ("traded_qty", "volume")):
                approved_mapping(db_session, feed.id, field, key)
        live_ = go_live(db_session, feed.id, hypercare_days=hypercare_days) if mapped else None
        db_session.commit()
        return feed, live_

    return _live


def loads_of(db_session, feed):
    from core.repositories.loads import LoadRepository

    return LoadRepository(db_session).list_for_feed(feed.id)


def subjects(db_session, recipient):
    from sqlalchemy import select

    from ports.notify.inapp import NotificationRow

    return [n.subject for n in db_session.scalars(select(NotificationRow).where(NotificationRow.recipient == recipient))]


def test_the_daily_job_loads_a_live_feed_after_its_fetch(db_session, daily, live):
    from core.repositories.loads import LoadRepository
    from core.repositories.standard_records import StandardRecordRepository

    feed, approved = live()
    assert daily().run() == {"syn_exchange_volumes": "fetched"}
    (scheduled, test_load) = loads_of(db_session, feed)
    assert (scheduled.purpose, scheduled.status, scheduled.go_live_id) == ("scheduled", "loaded", approved.id)
    assert scheduled.fetch_id == SourceFetchRepository(db_session).latest_fetched(feed.id).id
    assert [s.outcome for s in LoadRepository(db_session).stages(scheduled.id)] == ["stage_completed"] * 4
    assert StandardRecordRepository(db_session).count(scheduled.id) == 1  # the one row of the sample file
    assert test_load.purpose == "go_live_test"


def test_a_feed_without_a_go_live_is_fetched_but_not_loaded(db_session, daily, live):
    feed, _ = live(mapped=False)
    assert daily().run() == {"syn_exchange_volumes": "fetched"}
    assert loads_of(db_session, feed) == []


def test_when_the_source_is_down_there_is_no_load(db_session, daily, live):
    from ports.connectors.base import SourceUnavailable

    class Down:
        name = "down"

        def fetch(self, ref):
            raise SourceUnavailable(ref)

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    feed, _ = live()
    assert daily(lambda name: Down()).run() == {"syn_exchange_volumes": "source_down"}
    assert [load.purpose for load in loads_of(db_session, feed)] == ["go_live_test"]  # only the go-live's own


def test_under_hypercare_every_load_is_reported(db_session, daily, live):
    """Phase 6 plan, assumption 27: while today is at or before hypercare_until the owner and operations hear of
    each scheduled load; afterwards only a failure is reported (to operations and the backend team)."""
    import dataclasses

    feed, _ = live()
    job = dataclasses.replace(daily(), operations=lambda: ["dev-operations"])
    job.run()
    assert subjects(db_session, "dev-data-owner") == ["Load loaded: syn_exchange_volumes"]
    assert subjects(db_session, "dev-operations") == ["Load loaded: syn_exchange_volumes"]


def test_after_hypercare_a_good_load_is_not_reported(db_session, daily, live):
    import dataclasses

    live(hypercare_days=-1)  # it ended yesterday
    dataclasses.replace(daily(), operations=lambda: ["dev-operations"]).run()
    assert subjects(db_session, "dev-data-owner") == [] and subjects(db_session, "dev-operations") == []


def test_a_failed_load_is_reported_to_operations_and_the_backend_team_after_hypercare(db_session, daily, live,
                                                                                     sources):
    import dataclasses

    live(hypercare_days=-1)
    (sources / "files" / "exchange_volumes.csv").write_bytes(b"instr_id,trade_date,traded_qty\nsyn-1,2026-11-02,12x\n")
    dataclasses.replace(daily(), operations=lambda: ["dev-operations"]).run()
    assert subjects(db_session, "dev-operations") == ["Load failed: syn_exchange_volumes"]
    assert subjects(db_session, "dev-backend-team") == ["Load failed: syn_exchange_volumes"]
    assert subjects(db_session, "dev-data-owner") == []  # not under hypercare, and not a configuration change


def test_a_configuration_change_after_go_live_fails_the_load_and_tells_the_owner(db_session, daily, live):
    import dataclasses

    from tests.configured import approved_mapping

    feed, _ = live(hypercare_days=-1)
    approved_mapping(db_session, feed.id, "traded_qty", "lot_size")  # a steward re-maps after go-live
    db_session.commit()
    dataclasses.replace(daily(), operations=lambda: ["dev-operations"]).run()
    (failed, _test) = loads_of(db_session, feed)
    assert (failed.status, failed.failed_stage) == ("failed_rolled_back", "raw")
    assert subjects(db_session, "dev-data-owner") == ["Load failed: syn_exchange_volumes"]
    assert "new go-live" in db_session.execute(text_of_notification(feed)).scalar_one()


def text_of_notification(feed):
    from sqlalchemy import text

    return text("SELECT body FROM notifications WHERE recipient = 'dev-data-owner' AND subject LIKE 'Load failed%'")


def test_load_now_fetches_and_loads_one_feed_and_needs_a_go_live(db_session, daily, live):
    from core.repositories.errors import NotLive

    feed, _ = live()
    other, _none = live(code="syn_other_live", mapped=False)
    job = daily()
    with pytest.raises(NotLive):
        job.fetch_and_load(other.id)
    assert job.fetch_and_load(feed.id) == {"fetch": "fetched", "load": "loaded"}
    assert [load.purpose for load in loads_of(db_session, feed)] == ["scheduled", "go_live_test"]


def test_a_load_that_meets_one_in_progress_does_not_start_a_second(db_session, daily, live):
    from core.repositories.loads import LoadRepository

    feed, approved = live()
    LoadRepository(db_session).create(feed_id=feed.id, file_ref="raw/x.csv", purpose="scheduled",
                                      go_live_id=approved.id)  # operations' load is running
    db_session.commit()
    assert daily().run() == {"syn_exchange_volumes": "fetched"}
    assert [(load.purpose, load.status) for load in loads_of(db_session, feed)] == [
        ("scheduled", "received"), ("go_live_test", "loaded")]  # the job found it in progress and logged it
    assert daily().load_feed(feed.id) == "load_in_progress"


async def test_the_app_under_test_runs_no_worker_of_its_own():
    """Phase 5 plan, assumption 48: running_app puts a do-nothing worker in app.state.glossary_runs, and the
    start-up kick reaches it, not a worker on the application database."""
    from apps.api.main import app
    from tests.conftest import NoGlossaryRuns

    async with running_app():
        worker = app.state.glossary_runs
        assert isinstance(worker, NoGlossaryRuns)
        assert worker.kicked.wait(5)


async def test_the_worker_is_kicked_at_every_start_up():
    """Runs left by the last process are taken at the next start-up (assumption 11)."""
    from apps.api.main import app
    from tests.conftest import NoGlossaryRuns

    for _ in range(2):
        worker = NoGlossaryRuns()
        async with running_app(glossary_runs=worker):
            assert app.state.glossary_runs is worker and worker.kicked.wait(5)
