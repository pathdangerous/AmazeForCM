"""Review fixes 1 and 2, on real sessions in their own schema (the API's real session factory).

1. Every step that changes a requirement locks its row first: two concurrent picks give one success and
   one 409, never a 500.
2. No database connection is held during a model call: the API builds the agent's input, ends the read
   transaction, calls the model, then locks the requirement, checks its status again and writes. A
   status change during the call gives 409 and writes nothing.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

import pytest

from ports.llm.mock import MockProvider
from tests.test_commit_visibility import BLAKE, RILEY, committed, flow, own_schema  # noqa: F401 (fixtures)

pytestmark = pytest.mark.anyio


class WatchingProvider:
    """The mock provider, checking the pool on every call; optionally doing something during the call."""

    name = "mock"

    def __init__(self, engine, during_call=None):
        self.engine, self.during_call, self.checked_out = engine, during_call, []

    def complete(self, **kwargs):
        self.checked_out.append(self.engine.pool.checkedout())
        if self.during_call is not None:
            self.during_call()
        return MockProvider().complete(**kwargs)


def use_provider(provider):
    from apps.api.deps import get_llm_provider_port
    from apps.api.main import app

    app.dependency_overrides[get_llm_provider_port] = lambda: provider


# ---------------------------------------------------------------- fix 1


async def test_two_concurrent_picks_give_one_success_and_one_409(own_schema, flow, monkeypatch):
    """Both requests wait at a barrier just after the requirement's status is read. With the row locked
    first, the second waits for the first to commit and then finds the source already selected (409)."""
    import core.steps.s3_pick as s3_pick
    from core.repositories.proposals import ProposalRepository

    barrier = threading.Barrier(2)

    class WaitingProposals(ProposalRepository):
        def get(self, proposal_id):
            try:
                barrier.wait(timeout=1.5)
            except threading.BrokenBarrierError:
                pass  # the other request is waiting for the row lock: go on alone
            return super().get(proposal_id)

    async with flow() as f:
        listed = await f.listed()
        monkeypatch.setattr(s3_pick, "ProposalRepository", WaitingProposals)
        bodies = [{"proposal_id": c["proposal_id"], "accepted_conditions": c["source"]["conditions"]}
                  for c in listed.values()]
        responses = await asyncio.gather(*(
            f.client.post(f"/api/consumer/requirements/{f.id}/pick", headers=RILEY, json=body) for body in bodies),
            return_exceptions=True)
    assert not [r for r in responses if isinstance(r, BaseException)], responses
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    assert committed(own_schema, "SELECT count(*) FROM source_choices WHERE requirement_id = :id", id=f.id) == [(1,)]


async def test_two_requirements_selecting_the_same_new_source_at_once_make_one_feed(own_schema, flow, monkeypatch):
    """Review fix 1 (second round): feed_for takes an advisory lock on the source code before it looks
    for the feed. Both requests wait at a barrier just after that look; without the lock both find no
    feed and both create one, and the second fails on the unique index (a 500)."""
    import core.steps.known_source as known_source
    from core.repositories.feeds import FeedRepository

    barrier = threading.Barrier(2)

    class WaitingFeeds(FeedRepository):
        def get_by_source(self, source_code):
            found = super().get_by_source(source_code)
            try:
                barrier.wait(timeout=1.5)
            except threading.BrokenBarrierError:
                pass  # the other request waits for the advisory lock: go on alone
            return found

    async with flow() as f:
        first = await f.raised("volume", ("volume",))
        second = await f.raised("volume", ("volume",))
        monkeypatch.setattr(known_source, "FeedRepository", WaitingFeeds)
        # return_exceptions: wait for both, so a failing request cannot leave the other's session open
        responses = await asyncio.gather(*(
            f.client.post(f"/api/consumer/requirements/{rid}/find-source", headers=RILEY) for rid in (first, second)),
            return_exceptions=True)
    assert not [r for r in responses if isinstance(r, BaseException)], responses
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert committed(own_schema, "SELECT count(*) FROM feeds WHERE source_code = 'syn_exchange_volumes'") == [(1,)]
    assert committed(own_schema, "SELECT count(*) FROM source_choices") == [(2,)]


# ---------------------------------------------------------------- review fix 3: the source fetch


def _selected_volume(engine):
    """A requirement selected to the known source syn_exchange_volumes, committed; returns its feed id."""
    from core.repositories.requirements import RequirementRepository
    from core.seed import seed_glossary  # noqa: F401 (own_schema seeded the glossary)
    from core.steps.known_source import find_known_source
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
    from ports.db import get_sessionmaker

    with get_sessionmaker(engine)() as session:
        requirement = RequirementRepository(session).create(requester_id="dev-requester", title="volume",
                                                            element_keys=["volume"])
        feed_id = find_known_source(session, requirement_id=requirement.id,
                                    catalogue=SourceCatalogue.load(DEFAULT_CATALOGUE)).feed.id
        session.commit()
    return feed_id


def _connector(on_fetch):
    from ports.config import REPO_ROOT
    from ports.connectors.local_file import LocalFileConnector

    class Spy(LocalFileConnector):
        def fetch(self, source_ref):
            on_fetch()
            return super().fetch(source_ref)

    return lambda name: Spy(REPO_ROOT / "fixtures" / "sources")


def _daily(engine, tmp_path, factory):
    from apps.api.jobs import DailyFetch
    from ports.db import get_sessionmaker
    from ports.storage.local import LocalBlobStore

    return DailyFetch(session_factory=get_sessionmaker(engine), blob_store=LocalBlobStore(tmp_path / "blob"),
                      connector_factory=factory, backend_team=lambda: ["dev-backend-team"])


def test_no_connection_is_held_during_the_connector_call(own_schema, tmp_path):
    _selected_volume(own_schema)
    seen = []
    assert _daily(own_schema, tmp_path, _connector(lambda: seen.append(own_schema.pool.checkedout()))).run() == {
        "syn_exchange_volumes": "fetched"}
    assert seen == [0]


async def test_no_connection_is_held_during_the_first_fetch(own_schema, flow):
    from apps.api.deps import get_connector_factory
    from apps.api.main import app

    seen = []
    async with flow() as f:
        app.dependency_overrides[get_connector_factory] = lambda: _connector(
            lambda: seen.append(own_schema.pool.checkedout()))
        await f.raised("volume", ("volume",))
        found = await f.step("find-source")
    assert found["fetch"]["outcome"] == "fetched" and seen == [0]


async def test_a_release_and_a_fetch_of_the_same_feed_at_once_both_succeed(own_schema, flow, tmp_path, monkeypatch):
    """Independent review, fix 1. The fetch locks the feed row, then each requirement that selects it. A
    release locks the requirement, then inserts a source_choices row whose foreign key takes a key-share
    lock on the feed. With FOR UPDATE on the feed each waits for the other and PostgreSQL aborts one (a
    500 on the release, or a lost fetch). FOR NO KEY UPDATE does not conflict with a key-share lock.

    The fetch holds the feed lock until the release has been sent and has had time to reach its insert."""
    import core.steps.s4_fetch as s4_fetch
    from core.repositories.source_fetches import SourceFetchRepository

    feed_locked, release_sent = threading.Event(), threading.Event()

    class WaitingFetches(SourceFetchRepository):
        def record(self, **kwargs):
            row = super().record(**kwargs)
            feed_locked.set()  # record_fetch holds the feed row lock now
            release_sent.wait(timeout=5)
            time.sleep(0.5)  # the release reaches its insert meanwhile
            return row

    _selected_volume(own_schema)
    [(requirement_id,)] = committed(own_schema, "SELECT id FROM requirements")
    monkeypatch.setattr(s4_fetch, "SourceFetchRepository", WaitingFetches)
    results = {}
    job = threading.Thread(target=lambda: results.update(_daily(own_schema, tmp_path, _connector(lambda: None)).run()))
    async with flow() as f:
        job.start()
        assert await asyncio.to_thread(feed_locked.wait, 5)
        release_sent.set()
        try:
            response = await f.client.post(f"/api/consumer/requirements/{requirement_id}/release", headers=RILEY)
        except Exception as exc:  # noqa: BLE001 (a deadlock reaches the test as an exception; reported below)
            response = exc
        finally:
            await asyncio.to_thread(job.join, 30)  # never leave the job's session open: the schema is dropped
    assert not isinstance(response, Exception), repr(response)
    assert response.status_code == 200, response.text
    assert results == {"syn_exchange_volumes": "fetched"}
    assert committed(own_schema, "SELECT status FROM requirements") == [("no_source_found",)]
    assert committed(own_schema, "SELECT action FROM source_choices ORDER BY version") == [("selected",), ("released",)]


def test_the_first_fetch_and_the_daily_job_at_once_send_one_notification(own_schema, tmp_path):
    """Both fetch at once (a barrier inside the connector). The notification is decided after the fetch,
    with the feed row locked, so only the first to write sees a feed with no file yet."""
    from apps.api.routers.consumer import _first_fetch
    from ports.config import Settings
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
    from ports.db import get_sessionmaker
    from ports.identity.dev import DevIdentity
    from ports.notify.inapp import InAppNotifier
    from ports.storage.local import LocalBlobStore

    feed_id = _selected_volume(own_schema)
    barrier = threading.Barrier(2)
    factory = _connector(lambda: barrier.wait(timeout=10))
    errors = []

    def first_fetch():  # what POST .../find-source runs after committing the selection
        try:
            with get_sessionmaker(own_schema)() as session:
                _first_fetch(session, feed_id, identity=DevIdentity(), catalogue=SourceCatalogue.load(DEFAULT_CATALOGUE),
                             notifier=InAppNotifier(session), blob_store=LocalBlobStore(tmp_path / "api-blob"),
                             connector_factory=factory, settings=Settings(_env_file=None))
        except Exception as exc:  # noqa: BLE001 (reported below)
            errors.append(exc)

    api = threading.Thread(target=first_fetch)
    api.start()
    job_result = _daily(own_schema, tmp_path, factory).run()
    api.join(timeout=30)
    assert errors == [] and job_result == {"syn_exchange_volumes": "fetched"}
    assert committed(own_schema, "SELECT count(*) FROM source_fetches") == [(2,)]
    assert committed(own_schema, "SELECT count(*) FROM notifications WHERE subject LIKE 'File ready%'") == [(1,)]


# ---------------------------------------------------------------- fix 2


async def test_no_connection_is_held_during_any_model_call(own_schema, flow):
    provider = WatchingProvider(own_schema)
    async with flow() as f:
        use_provider(provider)
        await f.listed()  # find-source (S1), scope decision, sources (S2)
        await f.raised("yield to maturity", ("yield_to_maturity",))
        await f.step("find-source")
        await f.step("scope/decision", {"decision": "rejected"})
        await f.step("scope", {"note": "internal desk data"})  # S1 again
        await f.post("/api/consumer/search/assist", {"q": "closing prices"})
        await f.raised("volume", ("volume",))  # phase 4: the ProfilerAgent's call
        await f.step("find-source")
        await f.amaze("syn_exchange_volumes", "profile")
        await f.amaze("syn_exchange_volumes", "profile/summary")
    # IntentAgent x3, SourceDiscoveryAgent, DiscoveryAssistant, ProfilerAgent
    assert provider.checked_out == [0, 0, 0, 0, 0, 0]


async def test_a_new_profile_during_the_summary_gives_409_and_a_discard_row(own_schema, flow):
    """Phase 4 plan, assumption 16: the summary's before_write locks the feed and finds a newer profile."""
    from ports.db import get_sessionmaker
    from tests.onboarded import profiled

    async with flow() as f:
        await f.raised("volume", ("volume",))
        await f.step("find-source")
        await f.amaze("syn_exchange_volumes", "profile")

        def newer_profile():  # another person profiles the feed while the model answers, on its own session
            with get_sessionmaker(own_schema)() as session:
                profiled(session, f.feed_id)
                session.commit()

        use_provider(WatchingProvider(own_schema, during_call=newer_profile))
        response = await f.client.post(f"/api/amaze/feeds/{f.feed_id}/profile/summary",
                                       headers={"X-Dev-User": "dev-backend-team"}, json={})
    assert response.status_code == 409, response.text
    [(reason,)] = committed(own_schema, "SELECT reason FROM proposal_log_discards")
    assert reason.startswith("ProfileSuperseded")
    assert committed(own_schema, "SELECT count(*) FROM proposals WHERE kind = 'profile_summary'") == [(0,)]


@pytest.mark.parametrize("action", ["find-source", "sources"])
async def test_a_status_change_during_the_model_call_gives_409_and_logs_it_as_discarded(own_schema, flow, action):
    from sqlalchemy import text

    async with flow() as f:
        await f.raised()
        if action == "sources":
            await f.step("find-source")
            await f.step("scope/decision", {"decision": "approved"})
        before = committed(own_schema, "SELECT count(*) FROM proposal_log")

        def change_status():  # another request moves the requirement on, on its own connection
            with own_schema.begin() as connection:
                connection.execute(text("UPDATE requirements SET status = 'rejected' WHERE id = :id"), {"id": f.id})

        use_provider(WatchingProvider(own_schema, during_call=change_status))
        response = await f.client.post(f"/api/consumer/requirements/{f.id}/{action}", headers=RILEY, json={})
    assert response.status_code == 409, response.text
    # Exactly one committed call row, and one discard row pointing at it; no proposal, no status change.
    assert committed(own_schema, "SELECT count(*) FROM proposal_log") == [(before[0][0] + 1,)]
    [(reason, proposals)] = committed(
        own_schema, "SELECT d.reason, (SELECT count(*) FROM proposals p WHERE p.proposal_log_id = d.proposal_log_id) "
                    "FROM proposal_log_discards d")
    assert reason.startswith("RequirementNotInStatus") and proposals == 0
    assert committed(own_schema, "SELECT status FROM requirements WHERE id = :id", id=f.id) == [("rejected",)]


async def test_the_call_row_is_committed_before_the_request_writes(own_schema, flow, monkeypatch):
    """Review round 3, fix 2: when the step takes its lock (before_write), the call row is already
    committed, so no later failure of the request can lose it."""
    import core.steps.s1_scope as s1_scope

    seen = []
    original = s1_scope.locked_in

    def watching(session, requirement_id, allowed, what):
        seen.append(committed(own_schema, "SELECT count(*) FROM proposal_log")[0][0])
        return original(session, requirement_id, allowed, what)

    async with flow() as f:
        await f.raised()
        monkeypatch.setattr(s1_scope, "locked_in", watching)
        await f.step("find-source")
    assert seen == [1]


async def test_a_notifier_failure_after_the_call_leaves_its_call_row(own_schema, flow):
    """The agent lists no source, then telling the backend team fails: the request fails and is rolled
    back, but the call row was committed when the call ended. No discard row: the answer was used (it
    listed none); the request failed afterwards."""
    from apps.api.deps import get_request_notifier
    from apps.api.main import app

    class ListsNone(WatchingProvider):
        def complete(self, **kwargs):
            if kwargs["prompt_id"] == "source_discovery":
                from core.domain.sourcing import SourceDiscoveryOutput
                from ports.llm.base import LLMResult

                return LLMResult(output=SourceDiscoveryOutput(ranked=[]), provider="mock", model="mock",
                                 model_version="1", prompt_id="source_discovery", prompt_version="v1", latency_ms=0,
                                 raw_ref=None)
            return super().complete(**kwargs)

    class FailingNotifier:
        name = "failing"

        def notify(self, **kwargs):
            raise RuntimeError("notifier down")

    async with flow() as f:
        use_provider(ListsNone(own_schema))
        await f.raised()
        await f.step("find-source")
        await f.step("scope/decision", {"decision": "approved"})
        app.dependency_overrides[get_request_notifier] = lambda: FailingNotifier()
        with pytest.raises(RuntimeError, match="notifier down"):
            await f.client.post(f"/api/consumer/requirements/{f.id}/sources", headers=RILEY, json={})
    assert committed(own_schema, "SELECT agent_name FROM proposal_log ORDER BY created_at") == [
        ("IntentAgent",), ("SourceDiscoveryAgent",)]
    assert committed(own_schema, "SELECT count(*) FROM proposal_log_discards") == [(0,)]
    assert committed(own_schema, "SELECT status FROM requirements WHERE id = :id", id=f.id) == [("scope_confirmed",)]


# ---------------------------------------------------------------- phase 4: steps 02 to 04 on real sessions


def _lifecycle_with_a_file(engine, blobs):
    """A candidate feed of syn_instrument_lifecycle with one fetched file, committed; returns its id."""
    import uuid

    from core.repositories.feeds import FeedRepository
    from core.repositories.source_fetches import SourceFetchRepository
    from ports.config import REPO_ROOT
    from ports.db import get_sessionmaker

    with get_sessionmaker(engine)() as session:
        feed = FeedRepository(session).create_candidate(
            code="syn_instrument_lifecycle", name="Instrument lifecycle", source_code="syn_instrument_lifecycle",
            connector="local_file", source_ref="files/instrument_lifecycle.csv")
        key = f"raw/{feed.code}/2026-11-15/{uuid.uuid4()}/instrument_lifecycle.csv"
        content = (REPO_ROOT / "fixtures" / "sources" / "files" / "instrument_lifecycle.csv").read_bytes()
        blobs.put(key, content)
        SourceFetchRepository(session).record(feed_id=feed.id, outcome="fetched", blob_ref=key, size_bytes=len(content))
        session.commit()
        return feed.id


class _WatchedBlobs:
    """The local blob store, recording the pool on every get() and optionally waiting at a barrier."""

    def __init__(self, inner, engine, barrier=None):
        self.inner, self.engine, self.barrier, self.checked_out = inner, engine, barrier, []
        self.name = "watched"

    def put(self, key, data):
        return self.inner.put(key, data)

    def get(self, key):
        self.checked_out.append(self.engine.pool.checkedout())
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        return self.inner.get(key)


def _profile_on_its_own_session(engine, feed_id, blobs, errors, results):
    from core.steps.s02_profile import profile_plan, record_profile, run_profile
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
    from ports.db import get_sessionmaker

    try:
        with get_sessionmaker(engine)() as session:
            plan = profile_plan(session, feed_id=feed_id, catalogue=SourceCatalogue.load(DEFAULT_CATALOGUE))
            session.rollback()  # the read ends: no connection during DuckDB
            attempt = run_profile(plan, blob_store=blobs)
            results.append(record_profile(session, attempt, profiled_by="dev-backend-team").profile.version)
            session.commit()
    except Exception as exc:  # noqa: BLE001 (reported by the test)
        errors.append(exc)


def test_no_connection_is_held_during_a_profiling_run(own_schema, tmp_path):
    from ports.storage.local import LocalBlobStore

    blobs = _WatchedBlobs(LocalBlobStore(tmp_path / "blob"), own_schema)
    feed_id = _lifecycle_with_a_file(own_schema, blobs)
    errors, results = [], []
    _profile_on_its_own_session(own_schema, feed_id, blobs, errors, results)
    assert (errors, results, blobs.checked_out) == ([], [1], [0])


async def test_no_connection_is_held_during_the_profile_endpoint(own_schema, flow, tmp_path):
    """POST .../profile: the endpoint ends its read before DuckDB runs. The step-level test above calls the
    step functions itself, so only this test sees a change in the endpoint (checkpoint 3 review)."""
    from apps.api.deps import get_blob_store_port
    from apps.api.main import app
    from ports.storage.local import LocalBlobStore

    async with flow() as f:
        await f.raised("volume", ("volume",))
        await f.step("find-source")
        blobs = _WatchedBlobs(LocalBlobStore(tmp_path / "blob"), own_schema)
        app.dependency_overrides[get_blob_store_port] = lambda: blobs
        await f.amaze("syn_exchange_volumes", "profile")
    assert blobs.checked_out == [0]


def test_two_profiles_of_one_feed_at_once_take_the_next_versions(own_schema, tmp_path):
    """Both runs meet at a barrier inside BlobStore.get(), then record at once. record_profile locks the feed
    row before it reads the next version, so they write versions 1 and 2: two successes, never a unique
    violation. (Two profiles are both legitimate, so neither is a 409.)"""
    from ports.storage.local import LocalBlobStore

    blobs = _WatchedBlobs(LocalBlobStore(tmp_path / "blob"), own_schema, barrier=threading.Barrier(2))
    feed_id = _lifecycle_with_a_file(own_schema, blobs.inner)
    errors, results = [], []
    threads = [threading.Thread(target=_profile_on_its_own_session, args=(own_schema, feed_id, blobs, errors, results))
               for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert sorted(results) == [1, 2]
    assert committed(own_schema, "SELECT version FROM feed_profiles ORDER BY version") == [(1,), (2,)]


def _run_at_once(work, count=2):
    """Run work(i) on `count` threads that start together; returns (results, errors), each a list."""
    barrier, results, errors = threading.Barrier(count), [], []

    def one(i):
        try:
            barrier.wait(timeout=10)
            results.append(work(i))
        except Exception as exc:  # noqa: BLE001 (returned to the test)
            errors.append(exc)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results, errors


def _summarised_feed(engine, tmp_path, recommended="meets"):
    """The lifecycle feed with a profile and a pending summary, committed; returns its id."""
    from ports.db import get_sessionmaker
    from ports.storage.local import LocalBlobStore
    from tests.onboarded import profiled, summary

    feed_id = _lifecycle_with_a_file(engine, LocalBlobStore(tmp_path / "blob"))
    with get_sessionmaker(engine)() as session:
        summary(session, profiled(session, feed_id), recommended=recommended)
        session.commit()
    return feed_id


def test_two_signoffs_at_once_give_one_success_and_one_409(own_schema, tmp_path, monkeypatch):
    """What keeps two sign-offs apart is record_decision's lock on the summary's proposal row (FOR UPDATE):
    the second finds the summary decided (ProposalAlreadyDecided, 409). Never a unique violation on
    (feed_id, version). sign_off's source and feed locks are there for the lock order and the stage change,
    not for this race: with both removed this test still passes (plan, section 17, checkpoint 2). The
    profile read is made slow, so both reach the decision together when nothing else keeps them apart."""
    from core.repositories.errors import ProposalAlreadyDecided
    from core.repositories.feed_profiles import FeedProfileRepository

    _slow(monkeypatch, FeedProfileRepository, "latest")
    from core.steps.s03_signoff import sign_off
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
    from ports.db import get_sessionmaker
    from ports.notify.inapp import InAppNotifier
    from tests.conftest import dev_user_for

    feed_id = _summarised_feed(own_schema, tmp_path)

    def work(_):
        with get_sessionmaker(own_schema)() as session:
            result = sign_off(session, feed_id=feed_id, user=dev_user_for("backend_team"), outcome="meets", gaps=[],
                              reasons=[], catalogue=SourceCatalogue.load(DEFAULT_CATALOGUE),
                              notifier=InAppNotifier(session), backend_team=["dev-backend-team"])
            session.commit()
            return result.outcome

    results, errors = _run_at_once(work)
    assert results == ["meets"]
    assert [type(e) for e in errors] == [ProposalAlreadyDecided]
    assert committed(own_schema, "SELECT version, outcome FROM feed_signoffs") == [(1, "meets")]


def _slow(monkeypatch, cls, method, seconds=0.3, *, after=False) -> threading.Event:
    """Make one read slow, so two requests that are not kept apart by a lock both pass it before either writes.
    after: sleep after the read, so what it read can go stale before the write. Returns an event set inside
    the slowed read (just before the sleep): whoever calls it first is there, holding its locks."""
    original = getattr(cls, method)
    entered = threading.Event()

    def slow(self, *args, **kwargs):
        if not after:
            entered.set()
            time.sleep(seconds)
        result = original(self, *args, **kwargs)
        if after:
            entered.set()
            time.sleep(seconds)
        return result

    monkeypatch.setattr(cls, method, slow)
    return entered


def _first_then_second(first, second, entered: threading.Event):
    """Run first() on a thread; start second() on another once `entered` is set inside first's slowed read.
    Returns {name: result or error, "overlap": second started while first was still running}."""
    out, finished = {}, {}

    def run(name, work):
        try:
            out[name] = work()
        except Exception as exc:  # noqa: BLE001 (returned to the test)
            out[name] = exc
        finished[name] = time.monotonic()

    threads = [threading.Thread(target=run, args=("first", first)), threading.Thread(target=run, args=("second", second))]
    threads[0].start()
    assert entered.wait(timeout=10), "the first never reached its slowed read"
    second_started = time.monotonic()
    threads[1].start()
    for thread in threads:
        thread.join(timeout=30)
    out["overlap"] = "first" in finished and second_started < finished["first"]
    return out


def _in_session(engine, work):
    from ports.db import get_sessionmaker

    def run():
        with get_sessionmaker(engine)() as session:
            result = work(session)
            session.commit()
            return result

    return run


def _reject(engine, feed_id, reasons=("No vendor agreement on file",)):
    from core.steps.s03_signoff import sign_off
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
    from ports.notify.inapp import InAppNotifier
    from tests.conftest import dev_user_for

    return _in_session(engine, lambda s: sign_off(
        s, feed_id=feed_id, user=dev_user_for("backend_team"), outcome="rejected", gaps=[], reasons=list(reasons),
        catalogue=SourceCatalogue.load(DEFAULT_CATALOGUE), notifier=InAppNotifier(s),
        backend_team=["dev-backend-team"]).outcome)


def _summarise_for_rejection(engine, feed_id):
    from ports.db import get_sessionmaker
    from tests.onboarded import profiled, summary

    with get_sessionmaker(engine)() as session:
        summary(session, profiled(session, uuid.UUID(str(feed_id))), recommended="rejected")
        session.commit()


async def test_a_pick_and_a_rejection_of_one_source_at_once_leave_nobody_on_a_rejected_feed(own_schema, flow,
                                                                                           monkeypatch):
    """The rejection holds the source lock and the feed (its profile read slowed); a pick of the same source
    by another requirement waits for the source lock, then finds it rejected (SourceRejected, 409)."""
    from core.repositories.errors import SourceRejected
    from core.repositories.feed_profiles import FeedProfileRepository
    from core.steps.s3_pick import pick_source
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
    from tests.conftest import dev_user_for

    vendor_code = "syn_vendor_eod_prices"
    async with flow() as f:
        vendor = (await f.listed())[vendor_code]
        await f.step("pick", {"proposal_id": vendor["proposal_id"], "accepted_conditions": vendor["source"]["conditions"]})
        later = (await f.listed("open price again"))[vendor_code]
        await f.amaze(vendor_code, "profile")
    _summarise_for_rejection(own_schema, f.feed_id)
    entered = _slow(monkeypatch, FeedProfileRepository, "latest", seconds=0.5)
    pick = _in_session(own_schema, lambda s: pick_source(
        s, requirement_id=uuid.UUID(f.id), user=dev_user_for("requester"), proposal_id=uuid.UUID(later["proposal_id"]),
        accepted_conditions=_conditions(later), catalogue=SourceCatalogue.load(DEFAULT_CATALOGUE)).outcome)
    out = _first_then_second(_reject(own_schema, f.feed_id), pick, entered)
    assert out["overlap"], out
    assert out["first"] == "rejected", out
    assert isinstance(out["second"], SourceRejected), out
    assert committed(own_schema, "SELECT stage FROM feeds WHERE code = :c", c=vendor_code) == [("rejected",)]
    assert committed(own_schema, """
        SELECT count(*) FROM source_choices c
         WHERE c.version = (SELECT max(version) FROM source_choices x WHERE x.requirement_id = c.requirement_id)
           AND c.action = 'selected' AND c.feed_id = (SELECT id FROM feeds WHERE code = :c)""", c=vendor_code) == [(0,)]


async def _second_while_first_runs(first_request, second_request, entered):
    """Send the first request; send the second once `entered` is set inside the first's slowed read, while
    the first is still running. Returns (first response, second response)."""
    first = asyncio.create_task(first_request())
    assert await asyncio.to_thread(entered.wait, 10), "the first request never reached its slowed read"
    assert not first.done(), "no overlap: the first request had already finished"
    second = await second_request()
    return await first, second


async def test_two_signoffs_at_once_through_the_api_give_200_and_409(own_schema, flow, monkeypatch):
    """The endpoint, not only the step: the second sign-off is sent while the first holds its locks (its
    profile read slowed). One 200, one 409 (ProposalAlreadyDecided), never a 500; one sign-off committed."""
    from core.repositories.feed_profiles import FeedProfileRepository

    async with flow() as f:
        await f.raised("volume", ("volume",))
        await f.step("find-source")
        await f.amaze("syn_exchange_volumes", "profile")
        await f.amaze("syn_exchange_volumes", "profile/summary")
        entered = _slow(monkeypatch, FeedProfileRepository, "latest")
        url = f"/api/amaze/feeds/{f.feed_id}/signoff"

        def sign():
            return f.client.post(url, headers=BLAKE, json={"outcome": "meets"})

        first, second = await _second_while_first_runs(sign, sign, entered)
    assert (first.status_code, second.status_code) == (200, 409), (first.text, second.text)
    assert "already approved" in second.json()["detail"]
    assert committed(own_schema, "SELECT version, outcome FROM feed_signoffs") == [(1, "meets")]


async def test_a_pick_and_a_rejection_at_once_through_the_api_give_no_500(own_schema, flow, monkeypatch):
    """The endpoints, not only the steps: the rejection holds the source lock and the feed (its profile read
    slowed); a pick of the same source by another requirement is sent then. The rejection 200, the pick
    409 (SourceRejected); nobody is left on the rejected feed."""
    from core.repositories.feed_profiles import FeedProfileRepository

    vendor_code = "syn_vendor_eod_prices"
    async with flow() as f:
        vendor = (await f.listed())[vendor_code]
        await f.step("pick", {"proposal_id": vendor["proposal_id"], "accepted_conditions": vendor["source"]["conditions"]})
        later = (await f.listed("open price again"))[vendor_code]
        await f.amaze(vendor_code, "profile")
        await f.amaze(vendor_code, "profile/summary")
        entered = _slow(monkeypatch, FeedProfileRepository, "latest", seconds=0.5)

        def reject():
            return f.client.post(f"/api/amaze/feeds/{f.feed_id}/signoff", headers=BLAKE,
                                 json={"outcome": "rejected", "reasons": ["No vendor agreement on file"]})

        def pick():
            return f.client.post(f"/api/consumer/requirements/{f.id}/pick", headers=RILEY,
                                 json={"proposal_id": later["proposal_id"],
                                       "accepted_conditions": later["source"]["conditions"]})

        rejected, picked = await _second_while_first_runs(reject, pick, entered)
    assert (rejected.status_code, picked.status_code) == (200, 409), (rejected.text, picked.text)
    assert "rejected by the backend team" in picked.json()["detail"]
    assert committed(own_schema, "SELECT stage FROM feeds WHERE code = :c", c=vendor_code) == [("rejected",)]
    assert committed(own_schema, "SELECT status FROM requirements WHERE id = :id", id=f.id) == [
        ("awaiting_source_choice",)]


def _conditions(candidate):
    from ports.connectors.catalogue import SourceConditions

    return SourceConditions.model_validate(candidate["source"]["conditions"])


def _daily_fetch(engine, tmp_path):
    from ports.config import REPO_ROOT
    from ports.connectors.local_file import LocalFileConnector

    job = _daily(engine, tmp_path, lambda name: LocalFileConnector(REPO_ROOT / "fixtures" / "sources"))
    return lambda: job.run()


async def test_a_rejection_and_the_daily_fetch_of_one_feed_at_once_do_not_deadlock(own_schema, flow, tmp_path,
                                                                                   monkeypatch):
    """The fetch holds the feed (slowed just after its lock); the rejection starts, waits for the feed, and
    runs after. Both take the feed before any requirement, so neither deadlocks; both commit."""
    from core.repositories.source_fetches import SourceFetchRepository

    vendor_code = "syn_vendor_eod_prices"
    async with flow() as f:
        vendor = (await f.listed())[vendor_code]
        await f.step("pick", {"proposal_id": vendor["proposal_id"], "accepted_conditions": vendor["source"]["conditions"]})
        await f.amaze(vendor_code, "profile")
    _summarise_for_rejection(own_schema, f.feed_id)
    entered = _slow(monkeypatch, SourceFetchRepository, "latest", seconds=0.5)  # record_fetch: after the feed lock
    out = _first_then_second(_daily_fetch(own_schema, tmp_path), _reject(own_schema, f.feed_id), entered)
    assert out == {"first": {vendor_code: "fetched"}, "second": "rejected", "overlap": True}, out
    # released by the rejection; the backup is still listed
    assert committed(own_schema, "SELECT status FROM requirements WHERE id = :id", id=f.id) == [("awaiting_source_choice",)]


async def test_a_rejection_and_a_requester_release_of_one_requirement_at_once_do_not_deadlock(own_schema, flow,
                                                                                             monkeypatch):
    """The requester releases the source (slowed while the requirement is locked); the rejection waits for
    that requirement, finds it released meanwhile and skips it. Both commit; the feed is rejected."""
    from core.repositories.source_choices import SourceChoiceRepository
    from core.steps.release import release_source
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
    from ports.notify.inapp import InAppNotifier
    from tests.conftest import dev_user_for

    backup_code = "syn_backup_eod_prices"
    async with flow() as f:
        backup = (await f.listed())[backup_code]  # its file is missing: source_down, source_selected
        await f.step("pick", {"proposal_id": backup["proposal_id"], "accepted_conditions": backup["source"]["conditions"]})
        feeds = (await f.client.get("/api/amaze/feeds", headers=BLAKE)).json()
    feed_id = next(x["id"] for x in feeds if x["code"] == backup_code)
    _summarise_for_rejection(own_schema, feed_id)  # a profile on a fetched row written for the test
    entered = _slow(monkeypatch, SourceChoiceRepository, "record_release", seconds=0.5)
    release = _in_session(own_schema, lambda s: release_source(
        s, requirement_id=uuid.UUID(f.id), reason="requester_asked", user=dev_user_for("requester"),
        catalogue=SourceCatalogue.load(DEFAULT_CATALOGUE), notifier=InAppNotifier(s),
        backend_team=["dev-backend-team"]).reason)
    out = _first_then_second(release, _reject(own_schema, feed_id), entered)
    assert out == {"first": "requester_asked", "second": "rejected", "overlap": True}, out
    assert committed(own_schema, "SELECT stage FROM feeds WHERE id = :id", id=feed_id) == [("rejected",)]
    assert committed(own_schema, "SELECT version, action, reason FROM source_choices WHERE requirement_id = :id "
                                 "ORDER BY version", id=f.id) == [(1, "selected", None), (2, "released", "requester_asked")]


async def test_a_profile_during_a_signoff_waits_for_it_and_never_gives_a_500(own_schema, tmp_path, monkeypatch):
    """The sign-off locks the feed, reads the latest profile (slowed just after the read) and holds the lock;
    a profile of the same feed starts then, and its record_profile waits for that lock. So the sign-off
    succeeds on profile 1 and the profile is then version 2. Without the feed lock the profile commits first,
    the sign-off signs a profile that is no longer the latest, and the database refuses it (a raw
    IntegrityError: a 500)."""
    from core.repositories.feed_profiles import FeedProfileRepository
    from core.steps.s02_profile import profile_plan, record_profile, run_profile
    from core.steps.s03_signoff import sign_off
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
    from ports.notify.inapp import InAppNotifier
    from ports.storage.local import LocalBlobStore
    from tests.conftest import dev_user_for

    feed_id = _summarised_feed(own_schema, tmp_path)
    blobs = LocalBlobStore(tmp_path / "blob")
    catalogue = SourceCatalogue.load(DEFAULT_CATALOGUE)
    entered = _slow(monkeypatch, FeedProfileRepository, "latest", after=True)

    def profile(session):
        plan = profile_plan(session, feed_id=feed_id, catalogue=catalogue)
        session.rollback()
        return record_profile(session, run_profile(plan, blob_store=blobs), profiled_by="dev-backend-team").profile.version

    signoff = _in_session(own_schema, lambda s: sign_off(
        s, feed_id=feed_id, user=dev_user_for("backend_team"), outcome="meets", gaps=[], reasons=[],
        catalogue=catalogue, notifier=InAppNotifier(s), backend_team=["dev-backend-team"]).outcome)
    out = _first_then_second(signoff, _in_session(own_schema, profile), entered)
    assert out == {"first": "meets", "second": 2, "overlap": True}, out
    assert committed(own_schema, "SELECT s.version, p.version FROM feed_signoffs s "
                                 "JOIN feed_profiles p ON p.id = s.profile_id") == [(1, 1)]


async def test_a_pick_and_a_fetch_of_the_same_feed_at_once_both_succeed(own_schema, flow, tmp_path, monkeypatch):
    """The daily fetch holds the feed (FOR NO KEY UPDATE, slowed just after its lock); another requirement
    picks the same source: its source_choices insert takes FOR KEY SHARE on the feed, which does not wait.
    No deadlock; both commit."""
    from core.repositories.source_fetches import SourceFetchRepository
    from core.steps.s3_pick import pick_source
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
    from tests.conftest import dev_user_for

    vendor_code = "syn_vendor_eod_prices"
    async with flow() as f:
        vendor = (await f.listed())[vendor_code]
        await f.step("pick", {"proposal_id": vendor["proposal_id"], "accepted_conditions": vendor["source"]["conditions"]})
        later = (await f.listed("open price again"))[vendor_code]
    entered = _slow(monkeypatch, SourceFetchRepository, "latest", seconds=0.5)
    pick = _in_session(own_schema, lambda s: pick_source(
        s, requirement_id=uuid.UUID(f.id), user=dev_user_for("requester"), proposal_id=uuid.UUID(later["proposal_id"]),
        accepted_conditions=_conditions(later), catalogue=SourceCatalogue.load(DEFAULT_CATALOGUE)).outcome)
    started = time.monotonic()
    out = _first_then_second(_daily_fetch(own_schema, tmp_path), pick, entered)
    assert out == {"first": {vendor_code: "fetched"}, "second": "source_picked", "overlap": True}, out
    # the pick committed while the fetch waited; the fetch then read who selects the feed, so it is onboarding
    assert committed(own_schema, "SELECT status FROM requirements WHERE id = :id", id=f.id) == [("onboarding",)]
    assert time.monotonic() - started < 5


def test_two_registrations_at_once_give_one_success_and_one_409(own_schema, tmp_path, monkeypatch):
    """register_feed locks the feed row before it reads the stage, the sign-off and the next version: the
    second waits, then finds the feed registered (FeedNotACandidate, 409). Never a unique violation. The
    sign-off read is made slow, so without the lock both would pass it and write version 1."""
    from core.repositories.feed_signoffs import FeedSignoffRepository

    _slow(monkeypatch, FeedSignoffRepository, "latest")
    from core.repositories.errors import FeedNotACandidate
    from core.steps.s04_register import register_feed
    from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
    from ports.db import get_sessionmaker
    from ports.identity.dev import DevIdentity
    from ports.notify.inapp import InAppNotifier
    from tests.conftest import dev_user_for
    from tests.onboarded import signed_off

    from ports.storage.local import LocalBlobStore

    feed_id = _lifecycle_with_a_file(own_schema, LocalBlobStore(tmp_path / "blob"))
    with get_sessionmaker(own_schema)() as session:
        signed_off(session, feed_id)
        session.commit()

    def work(i):
        with get_sessionmaker(own_schema)() as session:
            result = register_feed(session, feed_id=feed_id, user=dev_user_for("backend_team"), owner_id="dev-data-owner",
                                   visibility="open", catalogue=SourceCatalogue.load(DEFAULT_CATALOGUE),
                                   identity=DevIdentity(), notifier=InAppNotifier(session))
            session.commit()
            return result.outcome

    results, errors = _run_at_once(work)
    assert results == ["registered"]
    assert [type(e) for e in errors] == [FeedNotACandidate]
    assert committed(own_schema, "SELECT version, owner_id FROM feed_registrations") == [(1, "dev-data-owner")]
