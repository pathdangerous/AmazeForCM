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
    # Exactly one committed log row for the call, written in its own transaction and marked discarded;
    # nothing else: no proposal, no status change.
    assert committed(own_schema, "SELECT count(*) FROM proposal_log") == [(before[0][0] + 1,)]
    [(reason, proposals)] = committed(
        own_schema, "SELECT l.discarded_reason, (SELECT count(*) FROM proposals p WHERE p.proposal_log_id = l.id) "
                    "FROM proposal_log l WHERE l.discarded_reason IS NOT NULL")
    assert reason.startswith("RequirementNotInStatus") and proposals == 0
    assert committed(own_schema, "SELECT status FROM requirements WHERE id = :id", id=f.id) == [("rejected",)]
