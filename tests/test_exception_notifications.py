"""Phase 7.14 T4: who is told, and the Overview counts (plan docs/plans/phase-7.14.md, sections 2.7 and 2.8).

The feed's registered owner is told when a load is held (with the number of open exception groups), when a waiver waits
for them, when groups are resolved (by agent proposal or by hand) and after a rerun. All through the Notifier; the
recipients are read from the database. The Overview counts groups resolved by agents and by hand, and the reruns.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from core.repositories.feed_registrations import FeedRegistrationRepository
from core.steps.exceptions import add_manual_fix, decide_exception_fixes, load_exceptions, tell_rerun
from core.steps.exceptions import RerunReleased
from ports.config import Settings
from ports.notify.inapp import InAppNotifier, NotificationRow
from tests.conftest import dev_user_for, running_app
from tests.configured import volume_csv
from tests.test_exception_fixes import NEGATIVE_RULE, a_held_load_with_three_kinds_of_group, agent, propose  # noqa: F401
from tests.test_exception_rerun import OWNER, STEWARD, group_of, template, waived  # noqa: F401 (template is a fixture)
from tests.test_exceptions_view import deliver, world  # noqa: F401 (world is a fixture)


def told(world, subject_start: str) -> list[NotificationRow]:
    return list(world.session.scalars(select(NotificationRow).where(NotificationRow.subject.like(f"{subject_start}%"))
                                      .order_by(NotificationRow.created_at, NotificationRow.id)))


def test_the_registered_owner_is_told_when_a_load_is_held_with_its_open_groups(world):
    owner = FeedRegistrationRepository(world.session).current(world.feed).owner_id
    held = a_held_load_with_three_kinds_of_group(world)
    [note] = [n for n in told(world, "Load held") if n.recipient == owner]
    assert "3 exception groups" in note.body and note.link == f"#/feed/{held.feed_id}"
    assert load_exceptions(world.session, held.id).total_groups == 3


def test_a_waiver_waiting_for_the_owner_tells_them_and_resolutions_tell_them_by_whom(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    propose(world, agent, held.id)
    notifier = InAppNotifier(world.session)
    groups = {g.rule_key: g for g in load_exceptions(world.session, held.id).groups}
    decide_exception_fixes(world.session, load_id=held.id, user=STEWARD, decision="approved", notifier=notifier,
                           group_keys=[g.group_key for g in groups.values()])
    [waits] = told(world, "Waivers wait")
    assert (waits.recipient, "1 waiver" in waits.body) == ("dev-data-owner", True)
    [resolved] = told(world, "Exception groups resolved")
    assert resolved.recipient == "dev-data-owner"
    assert "2 groups" in resolved.body and "2 by agent proposal" in resolved.body and "0 by hand" in resolved.body
    decide_exception_fixes(world.session, load_id=held.id, user=OWNER, decision="approved", notifier=notifier,
                           group_keys=[groups[NEGATIVE_RULE].group_key])
    last = told(world, "Exception groups resolved")[-1]
    assert "1 group" in last.body and "1 by agent proposal" in last.body


def test_a_fix_added_by_hand_is_counted_by_hand(world):
    held = a_held_load_with_three_kinds_of_group(world)
    key = group_of(world, held.id, NEGATIVE_RULE).group_key
    add_manual_fix(world.session, load_id=held.id, group_key=key, action="refer", reason="Ask.", user=STEWARD)
    decide_exception_fixes(world.session, load_id=held.id, user=STEWARD, decision="approved", group_keys=[key],
                           notifier=InAppNotifier(world.session))
    [resolved] = told(world, "Exception groups resolved")
    assert "1 by hand" in resolved.body and "0 by agent proposal" in resolved.body


def test_the_owner_and_the_consumers_are_told_how_a_rerun_ended(world):
    held = deliver(world, volume_csv(10, 2))
    result = RerunReleased(load_id=held.id, rerun_of=held.id, trust_score="100.0", previous_score="80.0", waived_rows=2)
    tell_rerun(world.session, result, feed_id=world.feed, notifier=InAppNotifier(world.session), consumers=["dev-requester"])
    notes = told(world, "Rerun")
    assert {n.recipient for n in notes} == {"dev-data-owner", "dev-requester"}
    assert all("released" in n.body and "100.0" in n.body for n in notes)


# ---------------------------------------------------------------- the Overview


@pytest.fixture
async def client(db_session, migrated_engine):
    async with running_app(engine=migrated_engine, session=db_session, settings=Settings(_env_file=None)) as c:
        yield c


@pytest.mark.anyio
async def test_the_overview_counts_fixes_by_agent_and_by_hand_and_the_open_groups(world, client, agent):
    headers = {"X-Dev-User": "dev-operator"}
    empty = (await client.get("/api/amaze/overview", headers=headers)).json()["exceptions"]
    assert empty == {"by_agent": 0, "by_hand": 0, "open": 0, "waiting_for_owner": 0, "reruns_released": 0,
                     "reruns_still_held": 0}
    held = a_held_load_with_three_kinds_of_group(world)
    propose(world, agent, held.id)
    groups = {g.rule_key: g for g in load_exceptions(world.session, held.id).groups}
    decide_exception_fixes(world.session, load_id=held.id, user=STEWARD, decision="approved",
                           group_keys=[g.group_key for g in groups.values()])
    world.session.commit()
    counts = (await client.get("/api/amaze/overview", headers=headers)).json()["exceptions"]
    assert (counts["by_agent"], counts["by_hand"], counts["waiting_for_owner"], counts["open"]) == (2, 0, 1, 0)
