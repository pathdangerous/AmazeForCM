"""Phase 7.15 T4: the audit endpoints (JSON and CSV), for any backend role."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import pytest

from core.steps.audit import CSV_COLUMNS
from ports.config import Settings
from tests.conftest import running_app
from tests.configured import VOLUME_RULE, volume_csv
from tests.test_commit_visibility import own_schema  # noqa: F401 (own_schema is a fixture)
from tests.test_exception_rerun import volume_template, waived  # noqa: F401
from tests.test_exceptions_view import deliver, world  # noqa: F401 (world is a fixture)

pytestmark = pytest.mark.anyio
OPERATOR = {"X-Dev-User": "dev-operator"}
MONTH = f"{datetime.now(UTC):%Y-%m}"


@pytest.fixture
async def client(db_session, migrated_engine):
    async with running_app(engine=migrated_engine, session=db_session, settings=Settings(_env_file=None)) as c:
        yield c


async def test_the_month_is_listed_as_json_and_filtered(world, client):
    held = deliver(world, volume_csv(10, 2))
    waived(world, held.id, VOLUME_RULE)
    world.session.commit()
    body = (await client.get(f"/api/amaze/audit?month={MONTH}", headers=OPERATOR)).json()
    assert body["month"] == MONTH and body["count"] == len(body["rows"]) >= 4
    owner = (await client.get(f"/api/amaze/audit?month={MONTH}&person=dev-data-owner", headers=OPERATOR)).json()
    assert {r["who"] for r in owner["rows"]} == {"dev-data-owner"}
    feed = (await client.get(f"/api/amaze/audit?month={MONTH}&feed={held.feed_id}", headers=OPERATOR)).json()
    assert {r["feed_code"] for r in feed["rows"]} == {"syn_exceptions"}


async def test_the_csv_is_the_same_rows_as_a_download(world, client):
    held = deliver(world, volume_csv(10, 2))
    waived(world, held.id, VOLUME_RULE)
    world.session.commit()
    response = await client.get(f"/api/amaze/audit.csv?month={MONTH}", headers=OPERATOR)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert f'filename="audit-{MONTH}.csv"' in response.headers["content-disposition"]
    lines = list(csv.reader(io.StringIO(response.text)))
    assert lines[0] == list(CSV_COLUMNS)
    assert len(lines) - 1 == (await client.get(f"/api/amaze/audit?month={MONTH}", headers=OPERATOR)).json()["count"]


async def test_a_decision_shows_the_code_version_in_force_when_it_was_made(own_schema, tmp_path):
    """On committed data (each step its own transaction), so the decisions come after the code versions."""
    from ports.db import get_sessionmaker
    from tests.configured import go_live
    from tests.test_api_exceptions import held_on

    feed, load = held_on(own_schema, tmp_path)
    steward = {"X-Dev-User": "dev-data-steward"}

    async def refer(client, rule):
        view = (await client.get(f"/api/amaze/loads/{load}/exceptions", headers=steward)).json()
        key = next(g["group_key"] for g in view["groups"] if g["rule_key"] == rule)
        await client.post(f"/api/amaze/loads/{load}/exceptions/fixes", headers=steward,
                          json={"group_key": key, "action": "refer", "reason": f"Ask about {rule}."})
        done = await client.post(f"/api/amaze/loads/{load}/exceptions/decision", headers=steward,
                                 json={"group_keys": [key], "decision": "approved"})
        assert done.status_code == 200, done.text

    from tests.test_commit_visibility import committed

    [(before,)] = committed(own_schema, "SELECT max(version) FROM code_versions")
    async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
        await refer(client, VOLUME_RULE)
        with get_sessionmaker(own_schema)() as session:
            go_live(session, feed)  # the owner approves a new go-live: code version 2
            session.commit()
        await refer(client, "volume_not_negative")
        rows = (await client.get(f"/api/amaze/audit?month={MONTH}&feed={feed}", headers=OPERATOR)).json()["rows"]
    mine = [r for r in rows if r["item"] == "exception_fix"]
    assert [(r["summary"], r["code_version"]) for r in mine] == [
        (f"Ask about {VOLUME_RULE}.", before), ("Ask about volume_not_negative.", before + 1)]


async def test_a_bad_month_is_422_and_the_audit_is_backend_only(client):
    assert (await client.get("/api/amaze/audit?month=2026-13", headers=OPERATOR)).status_code == 422
    assert (await client.get("/api/amaze/audit.csv?month=nope", headers=OPERATOR)).status_code == 422
    assert (await client.get(f"/api/amaze/audit?month={MONTH}", headers={"X-Dev-User": "dev-requester"})).status_code == 403
    assert (await client.get(f"/api/amaze/audit.csv?month={MONTH}")).status_code == 401
