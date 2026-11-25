"""Phase 7.21, T3/T4: GET /api/amaze/feeds/{id}/deliveries and GET /api/amaze/deliveries/overview (plan 2.12 to 2.14).

Read only. Loads are put on days before today's real date at 00:30 UTC, and the schedule is daily at 00:00 with 1440 minutes of
grace, so today's delivery is always still awaited and every figure is the same whatever the time of day the test runs."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from core.domain.schedule import ScheduleInput
from core.repositories.feeds import FeedRepository
from core.steps.schedule import set_schedule
from ports.config import Settings
from tests.configured import certified_load
from tests.conftest import dev_user_for, running_app
from tests.onboarded import registered_feed

pytestmark = pytest.mark.anyio

TEAM = {"X-Dev-User": "dev-backend-team"}
STEWARD = {"X-Dev-User": "dev-data-steward"}
REQUESTER = {"X-Dev-User": "dev-requester"}
DAILY = ScheduleInput(kind="recurring", frequency="daily", expected_time=time(0, 0), grace_minutes=1440)
TODAY = datetime.now(UTC).replace(hour=0, minute=30, second=0, microsecond=0)


@pytest.fixture
async def client(db_session, migrated_engine):
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None), session=db_session) as c:
        yield c


def feed_with(session, code, schedule: ScheduleInput | None, days_ago: list[int]):
    feed = FeedRepository(session).create_candidate(code=code, name=code.replace("_", " ").capitalize())
    registered_feed(session, feed.id, scheduled=False)
    if schedule is not None:
        set_schedule(session, feed_id=feed.id, schedule=schedule, user=dev_user_for("backend_team"))
    for k in days_ago:
        certified_load(session, feed.id, "released", rows=10 + k, received_at=TODAY - timedelta(days=k))
    session.commit()
    return feed.id


async def test_a_recurring_feed_returns_deliveries_shown_with_figures_that_equal_its_rows(client, db_session):
    feed = feed_with(db_session, "syn_api_recurring", DAILY, [4, 3, 1])  # the 2nd day back: nothing came
    response = await client.get(f"/api/amaze/feeds/{feed}/deliveries", headers=STEWARD)  # any backend role
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "deliveries_shown" and body["window"] == "30" and body["schedule"]["version"] == 1
    rows = body["rows"]
    assert [r["status"] for r in rows] == ["on_time", "on_time", "missing", "on_time", "awaited"]
    assert [r["rows"] for r in rows] == [14, 13, None, 11, None] and rows[0]["result"] == "released"
    f = body["figures"]
    assert (f["expected"], f["on_time"], f["late"], f["missing"], f["on_time_pct"]) == (4, 3, 0, 1, 75.0)
    assert f["expected"] == len([r for r in rows if r["timing"] != "awaited"]) and f["streak"] == 1
    assert f["next_expected"] == rows[-1]["due_at"]


@pytest.mark.parametrize("window", ["30", "90", "all"])
async def test_every_window_is_answered(client, db_session, window):
    feed = feed_with(db_session, f"syn_api_window_{window}", DAILY, [2])
    body = (await client.get(f"/api/amaze/feeds/{feed}/deliveries", params={"window": window}, headers=TEAM)).json()
    assert body["outcome"] == "deliveries_shown" and body["window"] == window and len(body["rows"]) == 3


async def test_a_one_time_feed_and_a_feed_with_no_schedule_have_their_own_typed_outcomes(client, db_session):
    once = feed_with(db_session, "syn_api_one_time", ScheduleInput(kind="one_time"), [3])
    shown = (await client.get(f"/api/amaze/feeds/{once}/deliveries", headers=TEAM)).json()
    assert shown["outcome"] == "one_time_shown" and shown["label"] == "One-time load"
    assert sorted(load["purpose"] for load in shown["loads"]) == ["go_live_test", "scheduled"]
    assert "figures" not in shown and "rows" not in shown
    old = feed_with(db_session, "syn_api_old", None, [])
    assert (await client.get(f"/api/amaze/feeds/{old}/deliveries", headers=TEAM)).json() == {
        "outcome": "schedule_not_set", "feed_id": str(old)}


async def test_unknown_feed_bad_window_and_roles(client, db_session):
    import uuid

    feed = feed_with(db_session, "syn_api_roles", DAILY, [])
    assert (await client.get(f"/api/amaze/feeds/{uuid.uuid4()}/deliveries", headers=TEAM)).status_code == 404
    assert (await client.get(f"/api/amaze/feeds/{feed}/deliveries", params={"window": "7"}, headers=TEAM)).status_code == 422
    assert (await client.get(f"/api/amaze/feeds/{feed}/deliveries", headers=REQUESTER)).status_code == 403
    assert (await client.get(f"/api/amaze/feeds/{feed}/deliveries")).status_code == 401
    assert (await client.get("/api/amaze/deliveries/overview", headers=REQUESTER)).status_code == 403


async def test_the_overview_lists_recurring_feeds_worst_first_and_counts_the_rest(client, db_session):
    feed_with(db_session, "syn_api_good", DAILY, [4, 3, 2, 1])
    feed_with(db_session, "syn_api_poor", DAILY, [4, 1])
    feed_with(db_session, "syn_api_nothing_yet", DAILY, [])
    feed_with(db_session, "syn_api_once", ScheduleInput(kind="one_time"), [])
    feed_with(db_session, "syn_api_unset", None, [])
    body = (await client.get("/api/amaze/deliveries/overview", headers=STEWARD)).json()
    assert body["outcome"] == "deliveries_overview" and (body["one_time"], body["not_set"]) == (1, 1)
    assert [(f["code"], f["on_time_pct"], f["missing"]) for f in body["feeds"]] == [
        ("syn_api_poor", 50.0, 2), ("syn_api_good", 100.0, 0), ("syn_api_nothing_yet", None, 0)]
    assert body["feeds"][0]["schedule"].startswith("Recurring, every day at 00:00 UTC")
