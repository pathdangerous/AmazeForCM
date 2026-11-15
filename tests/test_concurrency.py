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

import pytest

from ports.llm.mock import MockProvider
from tests.test_commit_visibility import RILEY, committed, flow, own_schema  # noqa: F401 (fixtures)

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
    assert provider.checked_out == [0, 0, 0, 0, 0]  # IntentAgent x3, SourceDiscoveryAgent, DiscoveryAssistant


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


def test_two_signoffs_at_once_give_one_success_and_one_409(own_schema, tmp_path):
    """sign_off locks the source code, then the feed row, then reads the latest version and the summary's
    status: the second waits, then finds the summary decided (ProposalAlreadyDecided, 409). Never a unique
    violation on (feed_id, version)."""
    from core.repositories.errors import ProposalAlreadyDecided
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
