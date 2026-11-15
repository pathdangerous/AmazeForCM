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
            f.client.post(f"/api/consumer/requirements/{f.id}/pick", headers=RILEY, json=body) for body in bodies))
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    assert committed(own_schema, "SELECT count(*) FROM source_choices WHERE requirement_id = :id", id=f.id) == [(1,)]


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
