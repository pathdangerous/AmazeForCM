"""Phase 7.9 API: POST /api/amaze/feeds/{id}/pause and /resume, DELETE /api/amaze/feeds/{id}, GET /api/amaze/feed-deletions.

Every named error is a 403, 404, 409 or 422, never a 500. Reason required; the feed code is typed to confirm a delete.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text

from ports.config import Settings
from tests.configured import loadable_feed
from tests.conftest import running_app

pytestmark = pytest.mark.anyio

OWNER = {"X-Dev-User": "dev-data-owner"}
OPS = {"X-Dev-User": "dev-operations"}
TEAM = {"X-Dev-User": "dev-backend-team"}
RILEY = {"X-Dev-User": "dev-requester"}
REASON = "Synthetic: the vendor is changing its file layout."
BASE = "/api/amaze/feeds"


@pytest.fixture
async def world(db_session, glossary, migrated_engine, tmp_path):
    feed, live = loadable_feed(db_session)
    db_session.commit()
    settings = Settings(_env_file=None, local_blob_root=tmp_path / "blob", dropzone_root=tmp_path / "dropzone")
    async with running_app(engine=migrated_engine, settings=settings, session=db_session) as client:
        yield type("W", (), {"client": client, "feed": feed, "session": db_session})()


def stage(world) -> str:
    world.session.rollback()
    return world.session.execute(text("SELECT stage FROM feeds WHERE id = :f"), {"f": world.feed}).scalar_one_or_none()


async def post(world, path: str, headers, body, expect: int):
    response = await world.client.post(f"{BASE}/{world.feed}/{path}", headers=headers, json=body)
    world.session.rollback()
    assert response.status_code == expect, (response.status_code, response.text)
    return response.json()


async def test_the_owner_pauses_and_operations_resumes_and_the_feed_page_says_so(world):
    paused = await post(world, "pause", OWNER, {"reason": REASON}, 200)
    assert paused["result"]["outcome"] == "paused" and paused["feed"]["feed"]["stage"] == "paused"
    assert stage(world) == "paused"
    resumed = await post(world, "resume", OPS, {"reason": "Synthetic: the layout is stable."}, 200)
    assert resumed["result"]["outcome"] == "resumed" and resumed["feed"]["feed"]["stage"] == "registered"
    listed = (await world.client.get("/api/amaze/feeds", headers=OPS)).json()
    assert [f["stage"] for f in listed if f["id"] == str(world.feed)] == ["registered"]


async def test_the_refusals_are_named_and_never_a_500(world):
    await post(world, "resume", OWNER, {"reason": REASON}, 409)  # not paused
    await post(world, "pause", TEAM, {"reason": REASON}, 403)  # neither the owner nor operations
    await post(world, "pause", OWNER, {"reason": "   "}, 422)  # a blank reason
    await post(world, "pause", OWNER, {}, 422)  # no reason at all
    await post(world, "pause", OWNER, {"reason": REASON, "extra": 1}, 422)
    await post(world, "pause", OWNER, {"reason": "x" * 501}, 422)
    assert (await world.client.post(f"{BASE}/{world.feed}/pause", json={"reason": REASON})).status_code == 401
    assert (await world.client.post(f"{BASE}/{world.feed}/pause", headers=RILEY, json={"reason": REASON})).status_code == 403
    await post(world, "pause", OWNER, {"reason": REASON}, 200)
    await post(world, "pause", OWNER, {"reason": REASON}, 409)  # already paused
    other = await world.client.post(f"{BASE}/{UUID(int=4)}/pause", headers=OPS, json={"reason": REASON})
    assert other.status_code == 404


async def delete(world, headers, body, expect: int):
    response = await world.client.request("DELETE", f"{BASE}/{world.feed}", headers=headers, json=body)
    world.session.rollback()
    assert response.status_code == expect, (response.status_code, response.text)
    return response.json()


async def test_a_feed_is_deleted_after_its_code_is_typed_and_the_deletion_is_listed(world):
    await delete(world, OWNER, {"reason": REASON, "confirm_code": "syn_wrong"}, 422)  # the wrong code
    await delete(world, OWNER, {"reason": REASON}, 422)  # no code
    await delete(world, OWNER, {"reason": " ", "confirm_code": "syn_loadable"}, 422)
    await delete(world, TEAM, {"reason": REASON, "confirm_code": "syn_loadable"}, 403)
    assert stage(world) == "registered"
    done = await delete(world, OWNER, {"reason": REASON, "confirm_code": "syn_loadable"}, 200)
    assert done["outcome"] == "deleted" and done["code"] == "syn_loadable" and done["rows_deleted"]["feeds"] == 1
    assert stage(world) is None
    assert (await world.client.get(f"{BASE}/{world.feed}", headers=OPS)).status_code == 404
    listed = (await world.client.get("/api/amaze/feed-deletions", headers=TEAM)).json()
    assert [(d["code"], d["by"], d["reason"]) for d in listed] == [("syn_loadable", "dev-data-owner", REASON)]
    assert set(listed[0]) >= {"id", "at", "feed_id", "code", "name", "by", "reason", "rows_deleted"}
    assert (await world.client.get("/api/amaze/feed-deletions", headers=RILEY)).status_code == 403
    await delete(world, OPS, {"reason": REASON, "confirm_code": "syn_loadable"}, 404)  # already gone


async def test_a_feed_with_a_load_in_progress_is_a_409_and_stays(world):
    from sqlalchemy import select

    world.session.execute(text(
        "INSERT INTO loads (feed_id, file_ref, purpose, status, go_live_id) SELECT :f, 'raw/x/y.csv', 'scheduled', "
        "'received', id FROM feed_go_lives WHERE feed_id = :f LIMIT 1"), {"f": world.feed})
    world.session.commit()
    await delete(world, OWNER, {"reason": REASON, "confirm_code": "syn_loadable"}, 409)
    assert stage(world) == "registered"
    assert world.session.execute(select(text("count(*)")).select_from(text("feed_deletions"))).scalar_one() == 0

# the commit-visibility tests (a separate connection sees only committed rows) are in test_commit_visibility.py
