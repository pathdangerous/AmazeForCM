"""Phase 7.15 T2: the reports API on the shared session (negative cases), and its commit visibility on its own schema."""

from __future__ import annotations

import pytest

from ports.config import Settings
from tests.conftest import running_app
from tests.configured import VOLUME_RULE
from tests.reporting import loaded_on
from tests.test_commit_visibility import committed, own_schema  # noqa: F401 (own_schema is a fixture)
from tests.test_reports import at, feeds, sql  # noqa: F401 (feeds is a fixture)

pytestmark = pytest.mark.anyio
OPERATOR = {"X-Dev-User": "dev-operator"}
OWNER = {"X-Dev-User": "dev-data-owner"}
STEWARD = {"X-Dev-User": "dev-data-steward"}
BODY = {"kind": "daily", "period_start": "2026-11-02", "role": "data_owner"}


@pytest.fixture
async def client(db_session, migrated_engine):
    async with running_app(engine=migrated_engine, session=db_session, settings=Settings(_env_file=None)) as c:
        yield c


async def test_a_person_makes_a_new_version_of_their_own_report_and_reads_it(client, db_session, feeds):
    loaded_on(db_session, feeds[0], feeds[1], at("2026-11-02", 8), flags={VOLUME_RULE: 4})
    db_session.commit()
    made = await client.post("/api/amaze/reports", headers=OWNER, json=BODY)
    assert made.status_code == 200, made.text
    assert made.json()["result"]["outcome"] == "report_written" and made.json()["result"]["version"] == 1
    again = await client.post("/api/amaze/reports", headers=OWNER, json=BODY)
    assert again.json()["result"]["version"] == 2  # a person asking makes a new version
    listed = (await client.get("/api/amaze/reports", headers=OWNER)).json()
    assert [(r["version"], r["subject_id"], r["narrative_status"]) for r in listed] == [
        (2, "dev-data-owner", "written"), (1, "dev-data-owner", "written")]
    detail = (await client.get(f"/api/amaze/reports/{listed[0]['id']}", headers=STEWARD)).json()  # any backend role reads
    assert detail["numbers"]["totals"]["held"] == 1 and "1 held" in detail["narrative"]


async def test_another_persons_report_needs_operations_and_a_future_period_is_refused(client):
    assert (await client.post("/api/amaze/reports", headers=STEWARD,
                              json={**BODY, "subject_id": "dev-data-owner"})).status_code == 403
    assert (await client.post("/api/amaze/reports", headers=OPERATOR,
                              json={**BODY, "subject_id": "dev-data-owner"})).status_code == 200
    assert (await client.post("/api/amaze/reports", headers=OWNER,
                              json={**BODY, "period_start": "2999-01-01"})).status_code == 422
    assert (await client.post("/api/amaze/reports", headers=OWNER, json={
        "kind": "monthly", "period_start": "2026-11-02", "role": "data_owner"})).status_code == 422
    assert (await client.post("/api/amaze/reports", headers={"X-Dev-User": "dev-requester"}, json=BODY)).status_code == 403


async def test_an_unknown_report_is_404_and_the_list_is_backend_only(client):
    import uuid

    assert (await client.get(f"/api/amaze/reports/{uuid.uuid4()}", headers=OWNER)).status_code == 404
    assert (await client.get("/api/amaze/reports", headers={"X-Dev-User": "dev-requester"})).status_code == 403


async def test_making_a_report_commits_it_and_the_narrative_call(own_schema, tmp_path):
    from ports.db import get_sessionmaker
    from tests.configured import loadable_feed

    with get_sessionmaker(own_schema)() as session:
        feed, live = loadable_feed(session, "syn_report_commit")
        loaded_on(session, feed, live.id, at("2026-11-02", 8), flags={VOLUME_RULE: 4})
        session.commit()
    async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
        response = await client.post("/api/amaze/reports", headers=OWNER, json=BODY)
        assert response.status_code == 200, response.text
    assert committed(own_schema, "SELECT kind, subject_id, version, narrative_status, generated_by FROM reports") == [
        ("daily", "dev-data-owner", 1, "written", "dev-data-owner")]
    assert committed(own_schema, "SELECT count(*) FROM proposal_log WHERE agent_name = 'ReportSummariserAgent'") == [(1,)]
    assert committed(own_schema, "SELECT r.narrative_log_id = l.id FROM reports r, proposal_log l "
                                 "WHERE l.agent_name = 'ReportSummariserAgent'") == [(True,)]


# ---------------------------------------------------------------- phase 7.16 T0a: preview today so far

COUNTS = ("SELECT (SELECT count(*) FROM reports), (SELECT count(*) FROM notifications), (SELECT count(*) FROM proposal_log)")


async def test_the_preview_of_today_is_the_numbers_of_today_and_stores_nothing(client, db_session, feeds):
    from datetime import UTC, datetime

    loaded_on(db_session, feeds[0], feeds[1], datetime.now(UTC), flags={VOLUME_RULE: 4})
    db_session.commit()
    before = sql(db_session, COUNTS)
    got = await client.get("/api/amaze/reports/preview", headers=OWNER, params={"role": "data_owner"})
    assert got.status_code == 200, got.text
    numbers = got.json()
    today = datetime.now(UTC).date()
    assert (numbers["period"]["kind"], numbers["period"]["start"]) == ("daily", today.isoformat())
    assert (numbers["totals"]["loads"], numbers["totals"]["held"], numbers["totals"]["flagged_rows"]) == (1, 1, 4)
    assert sql(db_session, COUNTS) == before  # no reports row, no notification, no model call logged
    # a stored report of today is still refused: stored reports are past periods only
    assert (await client.post("/api/amaze/reports", headers=OWNER,
                              json={**BODY, "period_start": today.isoformat()})).status_code == 422
    assert sql(db_session, COUNTS) == before


async def test_a_person_previews_their_own_view_and_operations_may_preview_anyone(client):
    assert (await client.get("/api/amaze/reports/preview", headers=STEWARD,
                             params={"role": "data_owner", "subject": "dev-data-owner"})).status_code == 403
    assert (await client.get("/api/amaze/reports/preview", headers=OPERATOR,
                             params={"role": "data_owner", "subject": "dev-data-owner"})).status_code == 200
    assert (await client.get("/api/amaze/reports/preview", headers={"X-Dev-User": "dev-requester"},
                             params={"role": "data_owner"})).status_code == 403
