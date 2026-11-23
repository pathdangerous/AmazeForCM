"""The overview is read-only, backend-only and reports stored facts, not samples."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from core.repositories.feeds import FeedRepository
from core.repositories.loads import LoadRepository
from core.repositories.tables import FeedRow, LoadRow
from ports.config import Settings
from tests.conftest import running_app
from tests.onboarded import registered_feed
from tests.configured import logged_call, volume_csv
from tests.test_s15_certify import deliver, world  # reuse the valid, fully configured load fixture

pytestmark = pytest.mark.anyio
OPERATOR = {"X-Dev-User": "dev-operator"}


@pytest.fixture
async def client(db_session, migrated_engine):
    async with running_app(engine=migrated_engine, session=db_session, settings=Settings(_env_file=None)) as c:
        yield c


async def test_overview_empty_is_zero_and_unknown_quality_is_null(client):
    response = await client.get("/api/amaze/overview", headers=OPERATOR)
    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 7
    assert data["feeds_live"] == data["feeds_paused"] == data["loads_count"] == 0
    assert data["average_trust_score"] is None
    assert data["quality"] is None
    assert data["recent_loads"] == data["pending_decisions"] == []


@pytest.mark.parametrize("headers, status", [({}, 401), ({"X-Dev-User": "dev-requester"}, 403)])
async def test_overview_is_backend_only(client, headers, status):
    assert (await client.get("/api/amaze/overview", headers=headers)).status_code == status


async def test_overview_rejects_unsupported_period(client):
    assert (await client.get("/api/amaze/overview?days=365", headers=OPERATOR)).status_code == 422


async def test_overview_reads_real_loads_with_period_and_no_fabricated_rows(client, db_session):
    recent = FeedRepository(db_session).create_candidate(code="overview_recent", name="Recent")
    older = FeedRepository(db_session).create_candidate(code="overview_old", name="Older")
    registered_feed(db_session, recent.id)
    registered_feed(db_session, older.id)
    LoadRepository(db_session).create(
        feed_id=recent.id, file_ref="overview/recent.csv", purpose="go_live_test")
    LoadRepository(db_session).create(
        feed_id=older.id, file_ref="overview/older.csv", purpose="go_live_test",
        received_at=datetime.now(timezone.utc) - timedelta(days=8))
    before = (db_session.scalar(select(func.count()).select_from(FeedRow)),
              db_session.scalar(select(func.count()).select_from(LoadRow)))
    data = (await client.get("/api/amaze/overview?days=7", headers=OPERATOR)).json()
    assert data["feeds_live"] == 2
    assert data["loads_count"] == 1
    assert data["recent_loads"][0]["feed_code"] == "overview_recent"
    assert data["recent_loads"][0]["rows"] is None
    assert data["recent_loads"][0]["trust_score"] is None
    month = (await client.get("/api/amaze/overview?days=30", headers=OPERATOR)).json()
    assert month["loads_count"] == 2
    assert before == (db_session.scalar(select(func.count()).select_from(FeedRow)),
                      db_session.scalar(select(func.count()).select_from(LoadRow)))


async def test_quality_uses_certified_row_counts_not_average_percentages(client, world):
    deliver(world, volume_csv(10, 1))
    deliver(world, volume_csv(4, 0))
    data = (await client.get("/api/amaze/overview", headers=OPERATOR)).json()
    assert data["average_trust_score"] == 95
    assert data["quality"]["rows"] == 14
    assert data["quality"]["flagged_rows"] == 1
    assert data["quality"]["passing_rows"] == 13
    assert data["quality"]["pass_percent"] == pytest.approx(100 * 13 / 14)
    assert data["quality"]["by_feed"][0]["flagged_rows"] == 1


async def test_pending_decisions_are_real_and_limited_to_the_callers_roles(client, db_session):
    from core.repositories.proposals import ProposalRepository
    feed = FeedRepository(db_session).create_candidate(code="overview_queue", name="Queue")
    call = logged_call(db_session, "MappingAgent", "mapping")
    proposal = ProposalRepository(db_session).create(
        proposal_log_id=call.id, kind="mapping", feed_id=feed.id, autonomy="A2",
        required_approver_roles=["data_steward"],
        payload={"source_field": "ticker", "reason": "Matched the alias", "confidence": 0.91},
    )
    operator = (await client.get("/api/amaze/overview", headers=OPERATOR)).json()
    assert operator["pending_decisions"] == [{
        "proposal_id": str(proposal.id), "feed_id": str(feed.id), "feed_code": "overview_queue",
        "feed_name": "Queue", "session_id": None, "kind": "mapping",
        "summary": "Matched the alias", "agent": "MappingAgent", "confidence": 0.91,
    }]
    checker = (await client.get("/api/amaze/overview", headers={"X-Dev-User": "dev-checker"})).json()
    assert checker["pending_decisions"] == []


async def test_a_pending_manual_or_rule_made_item_is_in_the_queue_with_who_made_it(client, db_session):
    """7.12: an item with no logged call (a person's, or code's) must not vanish from the queue."""
    from core.repositories.proposals import ProposalRepository

    feed = FeedRepository(db_session).create_candidate(code="overview_made", name="Made")
    repo = ProposalRepository(db_session)
    by_hand = repo.create_manual(kind="mapping", feed_id=feed.id, author="dev-operator", required_approver_roles=["data_steward"],
                                 payload={"source_field": "px", "column_type": None, "candidates": ["x"], "recommended": "x",
                                          "reason": "By hand."})
    by_rules = repo.create_by_rule(kind="profile_action", feed_id=feed.id, required_approver_roles=["backend_team"],
                                   payload={"action": "map_new_column", "columns": ["c"], "reason": "New.", "applied_by": "agent"})
    queue = {p["proposal_id"]: p for p in (await client.get("/api/amaze/overview", headers=OPERATOR)).json()["pending_decisions"]}
    assert queue[str(by_hand.id)]["agent"] == "added by dev-operator"
    assert queue[str(by_rules.id)]["agent"] == "rules"


async def test_the_overview_counts_drift_events_and_actions_by_who_applies_them(client, db_session):
    from core.repositories.proposals import ProposalRepository
    from tests.conftest import dev_user_for

    feed = FeedRepository(db_session).create_candidate(code="overview_drift", name="Drift")
    registered_feed(db_session, feed.id)
    repo = ProposalRepository(db_session)
    for applied in ("agent", "agent", "manual"):
        p = repo.create_by_rule(kind="profile_action", feed_id=feed.id, required_approver_roles=["backend_team"],
                                payload={"action": "map_new_column", "columns": ["c"], "reason": "New.", "applied_by": applied})
        repo.record_decision(p.id, user=dev_user_for("backend_team"), decision="approved")
    repo.create_by_rule(kind="profile_action", feed_id=feed.id, required_approver_roles=["backend_team"],
                        payload={"action": "review_rule", "columns": [], "reason": "Up.", "applied_by": "manual"})
    data = (await client.get("/api/amaze/overview?days=30", headers=OPERATOR)).json()
    assert data["actions"] == {"by_agent": 2, "by_hand": 1, "pending": 1}
    assert data["drift_events"] == []  # no delivery drifted: nothing is invented
    assert (await client.get("/api/amaze/overview?days=1", headers=OPERATOR)).json()["days"] == 1
