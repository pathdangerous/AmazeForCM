"""Phase 7.14 T2: the exception fix endpoints on the test's shared session (negative cases), and their commit
visibility on a schema of their own (one test per committing endpoint, happy path only: tests/test_commit_visibility.py's
rule)."""

from __future__ import annotations

import pytest

from core.repositories.proposals import ProposalRepository
from ports.config import Settings
from tests.conftest import running_app
from tests.test_commit_visibility import committed, own_schema  # noqa: F401 (own_schema is a fixture)
from tests.test_exception_fixes import NEGATIVE_RULE, a_held_load_with_three_kinds_of_group
from tests.test_exceptions_view import world  # noqa: F401 (world is a fixture)

pytestmark = pytest.mark.anyio
STEWARD, OWNER = {"X-Dev-User": "dev-data-steward"}, {"X-Dev-User": "dev-data-owner"}


def held_and_committed(world):
    """run_scheduled() leaves its last write (the load held) in the open transaction; the API's read() rolls back,
    so commit it first (on the shared test session a commit only releases a savepoint)."""
    held = a_held_load_with_three_kinds_of_group(world)
    world.session.commit()
    return held


@pytest.fixture
async def client(db_session, migrated_engine):
    async with running_app(engine=migrated_engine, session=db_session, settings=Settings(_env_file=None)) as c:
        yield c


async def test_a_steward_proposes_fixes_and_others_may_not(world, client):
    held = held_and_committed(world)
    assert (await client.post(f"/api/amaze/loads/{held.id}/exceptions/propose", headers=OWNER)).status_code == 403
    response = await client.post(f"/api/amaze/loads/{held.id}/exceptions/propose", headers=STEWARD)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["outcome"] == "fixes_proposed"
    assert {g["state"] for g in body["exceptions"]["groups"]} == {"fix_pending"}
    again = await client.post(f"/api/amaze/loads/{held.id}/exceptions/propose", headers=STEWARD)
    assert again.json()["result"] == {"outcome": "no_fix_proposed", "log_id": None, "no_fix": []}


async def test_fixes_of_a_load_that_is_not_held_are_a_conflict(world, client):
    from tests.configured import volume_csv
    from tests.test_exceptions_view import deliver

    released = deliver(world, volume_csv(10, 0))
    world.session.commit()
    response = await client.post(f"/api/amaze/loads/{released.id}/exceptions/propose", headers=STEWARD)
    assert response.status_code == 409 and "only a held load" in response.json()["detail"]


async def test_a_waiver_is_decided_by_the_steward_then_the_owner_through_the_decision_route(world, client):
    held = held_and_committed(world)
    body = (await client.post(f"/api/amaze/loads/{held.id}/exceptions/propose", headers=STEWARD)).json()
    waive = next(p for p in map(ProposalRepository(world.session).get, body["result"]["proposal_ids"])
                 if p.payload.get("action") == "waive")
    early = await client.post(f"/api/amaze/proposals/{waive.id}/decision", headers=OWNER, json={"decision": "approved"})
    assert early.status_code == 403  # the steward comes first
    first = await client.post(f"/api/amaze/proposals/{waive.id}/decision", headers=STEWARD, json={"decision": "approved"})
    assert first.status_code == 200 and first.json()["result"]["waiting_for"] == "data_owner"
    done = await client.post(f"/api/amaze/proposals/{waive.id}/decision", headers=OWNER, json={"decision": "approved"})
    assert done.status_code == 200 and done.json()["result"]["resolution_id"] is not None


async def test_a_manual_fix_for_an_unknown_group_is_refused(world, client):
    held = held_and_committed(world)
    response = await client.post(f"/api/amaze/loads/{held.id}/exceptions/fixes", headers=STEWARD,
                                 json={"group_key": "0000000000000000", "action": "refer", "reason": "Ask."})
    assert response.status_code == 422


# ---------------------------------------------------------------- commit visibility (own schema)


def held_on(engine, tmp_path):
    """A held load of three kinds of group, committed on the engine's schema; returns (feed id, load id)."""
    from decimal import Decimal
    from types import SimpleNamespace

    from core.steps.s20_create_product import FinishContext
    from ports.db import get_sessionmaker
    from ports.notify.inapp import InAppNotifier
    from ports.storage.local import LocalBlobStore
    from tests.configured import loadable_feed

    with get_sessionmaker(engine)() as session:
        feed, live = loadable_feed(session, "syn_exceptions")
        w = SimpleNamespace(feed=feed, live=live, session=session, blobs=LocalBlobStore(tmp_path / "blob"), n=0,
                            context=FinishContext(notifier=InAppNotifier(session), threshold=Decimal("90"),
                                                  operations=[], backend_team=[]))
        held = a_held_load_with_three_kinds_of_group(w)
        from core.domain.profiling import FileColumn
        from tests.onboarded import profiled

        profiled(session, feed, columns=[  # step 12 compares a delivery with the feed's latest profile
            FileColumn(name=n, type=t, null_count=0, null_rate=0.0, distinct_count=1)
            for n, t in (("instr_id", "VARCHAR"), ("trade_date", "DATE"), ("traded_qty", "BIGINT"))])
        session.commit()
        return feed, held.id


async def test_propose_commits_the_fixes_and_the_call(own_schema, tmp_path):
    _feed, load = held_on(own_schema, tmp_path)
    async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
        response = await client.post(f"/api/amaze/loads/{load}/exceptions/propose", headers=STEWARD)
        assert response.status_code == 200, response.text
    assert committed(own_schema, "SELECT kind, count(*) FROM proposals WHERE kind IN ('exception_fix', 'transformation') "
                                 "GROUP BY kind ORDER BY kind") == [("exception_fix", 2), ("transformation", 1)]
    assert committed(own_schema, "SELECT count(*) FROM proposal_log WHERE agent_name = 'ExceptionFixAgent'") == [(1,)]
    assert committed(own_schema, "SELECT count(*) FROM proposal_log_discards") == [(0,)]


async def test_the_owners_decision_commits_the_waiver(own_schema, tmp_path):
    _feed, load = held_on(own_schema, tmp_path)
    async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
        body = (await client.post(f"/api/amaze/loads/{load}/exceptions/propose", headers=STEWARD)).json()
        [(waive,)] = committed(own_schema, "SELECT id FROM proposals WHERE payload->>'action' = 'waive'")
        assert str(waive) in body["result"]["proposal_ids"]
        for headers in (STEWARD, OWNER):
            response = await client.post(f"/api/amaze/proposals/{waive}/decision", headers=headers,
                                         json={"decision": "approved"})
            assert response.status_code == 200, response.text
    assert committed(own_schema, "SELECT action, rule_key, decided_by, applied_by FROM exception_resolutions") == [
        ("waived", NEGATIVE_RULE, "dev-data-owner", "agent")]


async def test_a_bulk_decision_commits_the_decisions(own_schema, tmp_path):
    _feed, load = held_on(own_schema, tmp_path)
    async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
        await client.post(f"/api/amaze/loads/{load}/exceptions/propose", headers=STEWARD)
        view = (await client.get(f"/api/amaze/loads/{load}/exceptions", headers=STEWARD)).json()
        keys = [g["group_key"] for g in view["groups"]]
        response = await client.post(f"/api/amaze/loads/{load}/exceptions/decision", headers=STEWARD,
                                     json={"group_keys": keys, "decision": "approved"})
        assert response.status_code == 200, response.text
        assert response.json()["result"]["outcome"] == "fixes_decided"
    assert committed(own_schema, "SELECT action FROM exception_resolutions ORDER BY action") == [("referred",)]
    assert committed(own_schema, "SELECT count(*) FROM transformations") == [(1,)]
    assert committed(own_schema, "SELECT count(*) FROM approvals a JOIN proposals p ON p.id = a.proposal_id "
                                 "WHERE a.decided_by = 'dev-data-steward' AND a.decision = 'approved' "
                                 "AND (p.kind = 'exception_fix' OR p.payload ? 'exception_group')") == [(3,)]


async def test_a_rerun_commits_a_new_recorded_load_and_leaves_the_held_one_alone(own_schema, tmp_path):
    from apps.api.deps import get_daily_fetch
    from apps.api.main import app
    from ports.storage.local import LocalBlobStore
    from tests.test_commit_visibility import daily_on

    _feed, load = held_on(own_schema, tmp_path)
    daily = daily_on(own_schema, LocalBlobStore(tmp_path / "blob"))
    async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
        app.dependency_overrides[get_daily_fetch] = lambda: daily
        view = (await client.get(f"/api/amaze/loads/{load}/exceptions", headers=STEWARD)).json()
        for rule in ("volume_over_100000", NEGATIVE_RULE):
            key = next(g["group_key"] for g in view["groups"] if g["rule_key"] == rule)
            await client.post(f"/api/amaze/loads/{load}/exceptions/fixes", headers=STEWARD,
                              json={"group_key": key, "action": "waive", "reason": "Real values."})
            for headers in (STEWARD, OWNER):
                done = await client.post(f"/api/amaze/loads/{load}/exceptions/decision", headers=headers,
                                         json={"group_keys": [key], "decision": "approved"})
                assert done.status_code == 200, done.text
        response = await client.post(f"/api/amaze/loads/{load}/rerun", headers=STEWARD,
                                     json={"reason": "Both are real."})
        assert response.status_code == 200, response.text
        assert response.json()["result"]["outcome"] == "rerun_still_held"  # 1 of 6 rows still flagged: 83.3
        assert (await client.post(f"/api/amaze/loads/{load}/rerun", headers=OWNER,
                                  json={"reason": "x"})).status_code == 403
    assert committed(own_schema, "SELECT status, rerun_reason, rerun_of_load_id = :l FROM loads "
                                 "WHERE rerun_of_load_id IS NOT NULL", l=load) == [("held", "Both are real.", True)]
    assert committed(own_schema, "SELECT status FROM loads WHERE id = :l", l=load) == [("held",)]
    assert committed(own_schema, "SELECT waived_rows, flagged_rows FROM load_certifications c JOIN loads l ON l.id = "
                                 "c.load_id WHERE l.rerun_of_load_id IS NOT NULL") == [(2, 1)]
    assert committed(own_schema, "SELECT count(*) FROM standard_records WHERE load_id = :l AND cardinality(waived) > 0",
                     l=load) == [(0,)]  # the held load's records were not touched


async def test_a_manual_fix_commits_the_proposal(own_schema, tmp_path):
    _feed, load = held_on(own_schema, tmp_path)
    async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
        view = (await client.get(f"/api/amaze/loads/{load}/exceptions", headers=STEWARD)).json()
        group = next(g for g in view["groups"] if g["rule_key"] == NEGATIVE_RULE)
        response = await client.post(f"/api/amaze/loads/{load}/exceptions/fixes", headers=STEWARD,
                                     json={"group_key": group["group_key"], "action": "refer", "reason": "Ask them."})
        assert response.status_code == 200, response.text
    assert committed(own_schema, "SELECT origin, author, payload->>'action' FROM proposals WHERE kind = 'exception_fix'") == [
        ("manual", "dev-data-steward", "refer")]
