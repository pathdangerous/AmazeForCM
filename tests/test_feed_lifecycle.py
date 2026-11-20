"""Phase 7.9: pause, resume and delete of a feed (core/steps/feed_lifecycle.py; plan sections 3 and 5).

Deterministic steps, no model call. The owner of the feed or a user with the operations role may act, with a reason;
every refusal is a named error (409, 403 or 422 at the API), never a bare database error.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from core.repositories.errors import (
    FeedNotFound, FeedNotLive, FeedNotPaused, LoadInProgress, NotOwnerOrOperations,
)
from core.repositories.feeds import FeedRepository
from core.repositories.requirements import RequirementRepository
from core.steps.errors import ReasonNeeded
from core.steps.feed_lifecycle import (
    FeedDeleted, FeedPaused, FeedResumed, delete_feed, pause_feed, resume_feed,
)
from core.steps.s20_create_product import FinishContext
from ports.identity.dev import DevIdentity
from ports.notify.inapp import InAppNotifier, NotificationRow
from ports.storage.local import LocalBlobStore
from tests.configured import loadable_feed, onboarding_requirement, volume_csv
from tests.step_chains import run_scheduled

IDENTITY = DevIdentity()
OWNER, OPERATIONS, OTHER = (IDENTITY.get_user(u) for u in ("dev-data-owner", "dev-operations", "dev-backend-team"))
REASON = "Synthetic: the vendor is changing its file layout."


@pytest.fixture
def world(db_session, glossary, tmp_path):
    feed, live = loadable_feed(db_session)  # registered, owner dev-data-owner, a go-live
    blobs = LocalBlobStore(tmp_path / "blob")
    context = FinishContext(notifier=InAppNotifier(db_session), threshold=Decimal("90"), operations=["dev-operations"],
                            backend_team=["dev-backend-team"])
    return SimpleNamespace(feed=feed, live=live, session=db_session, blobs=blobs, context=context,
                           dropzone=tmp_path / "dropzone", notifier=InAppNotifier(db_session), n=0)


def stage(world) -> str:
    world.session.expire_all()
    return FeedRepository(world.session).get(world.feed).stage


def events(world) -> list[tuple[str, str, str]]:
    return [tuple(r) for r in world.session.execute(text(
        "SELECT action, reason, \"by\" FROM feed_status_events WHERE feed_id = :f ORDER BY at"), {"f": world.feed})]


# ---------------------------------------------------------------- pause and resume: the allowed moves


def test_a_live_feed_is_paused_and_resumed_and_both_are_recorded(world):
    paused = pause_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON)
    assert isinstance(paused, FeedPaused) and paused.outcome == "paused" and stage(world) == "paused"
    resumed = resume_feed(world.session, feed_id=world.feed, user=OPERATIONS, reason="Synthetic: the layout is stable.")
    assert isinstance(resumed, FeedResumed) and resumed.outcome == "resumed" and stage(world) == "registered"
    assert events(world) == [("pause", REASON, "dev-data-owner"),
                             ("resume", "Synthetic: the layout is stable.", "dev-operations")]


def test_a_paused_feed_can_be_paused_and_resumed_again(world):
    for _ in range(2):
        pause_feed(world.session, feed_id=world.feed, user=OPERATIONS, reason=REASON)
        resume_feed(world.session, feed_id=world.feed, user=OPERATIONS, reason=REASON)
    assert stage(world) == "registered" and [e[0] for e in events(world)] == ["pause", "resume"] * 2


# ---------------------------------------------------------------- the refused moves


def candidate(world):
    return FeedRepository(world.session).create_candidate(code="syn_candidate", name="Candidate").id


def test_only_a_live_feed_is_paused(world):
    for feed_id in (candidate(world),):
        with pytest.raises(FeedNotLive):
            pause_feed(world.session, feed_id=feed_id, user=OPERATIONS, reason=REASON)
    pause_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON)
    with pytest.raises(FeedNotLive):  # already paused
        pause_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON)
    assert stage(world) == "paused" and len(events(world)) == 1  # the refusals wrote nothing


def test_only_a_paused_feed_is_resumed(world):
    with pytest.raises(FeedNotPaused):  # registered
        resume_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON)
    with pytest.raises(FeedNotPaused):  # a candidate
        resume_feed(world.session, feed_id=candidate(world), user=OPERATIONS, reason=REASON)
    assert stage(world) == "registered" and events(world) == []


def test_an_unknown_feed_is_not_found(world):
    from uuid import UUID

    for step in (pause_feed, resume_feed):
        with pytest.raises(FeedNotFound):
            step(world.session, feed_id=UUID(int=3), user=OPERATIONS, reason=REASON)


@pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
def test_a_reason_is_needed_for_every_move(world, reason):
    with pytest.raises(ReasonNeeded):
        pause_feed(world.session, feed_id=world.feed, user=OWNER, reason=reason)
    pause_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON)
    with pytest.raises(ReasonNeeded):
        resume_feed(world.session, feed_id=world.feed, user=OWNER, reason=reason)
    with pytest.raises(ReasonNeeded):
        delete_feed(world.session, feed_id=world.feed, user=OWNER, reason=reason, blob_store=world.blobs,
                    dropzone_root=world.dropzone, notifier=world.notifier)
    assert stage(world) == "paused"


def test_only_the_owner_or_operations_may_act(world):
    for step in (pause_feed,):
        with pytest.raises(NotOwnerOrOperations):
            step(world.session, feed_id=world.feed, user=OTHER, reason=REASON)
    assert stage(world) == "registered"
    pause_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON)  # the owner
    with pytest.raises(NotOwnerOrOperations):
        resume_feed(world.session, feed_id=world.feed, user=OTHER, reason=REASON)
    with pytest.raises(NotOwnerOrOperations):
        delete_feed(world.session, feed_id=world.feed, user=OTHER, reason=REASON, blob_store=world.blobs,
                    dropzone_root=world.dropzone, notifier=world.notifier)
    resume_feed(world.session, feed_id=world.feed, user=OPERATIONS, reason=REASON)  # operations


def test_a_feed_with_no_owner_is_acted_on_by_operations_only(world):
    feed_id = candidate(world)
    with pytest.raises(NotOwnerOrOperations):
        delete_feed(world.session, feed_id=feed_id, user=OWNER, reason=REASON, blob_store=world.blobs,
                    dropzone_root=world.dropzone, notifier=world.notifier)
    done = delete_feed(world.session, feed_id=feed_id, user=OPERATIONS, reason=REASON, blob_store=world.blobs,
                       dropzone_root=world.dropzone, notifier=world.notifier)
    assert isinstance(done, FeedDeleted)


# ---------------------------------------------------------------- the database refuses a move that was not recorded


def raw_stage(world, to: str):
    with world.session.begin_nested():
        world.session.execute(text("UPDATE feeds SET stage = :s WHERE id = :f"), {"s": to, "f": world.feed})


def test_the_database_allows_a_stage_move_only_right_after_its_own_event(world):
    with pytest.raises(DBAPIError, match="record the pause in feed_status_events first"):
        raw_stage(world, "paused")  # no event
    world.session.execute(text(
        "INSERT INTO feed_status_events (feed_id, action, reason, \"by\") VALUES (:f, 'resume', 'x', 'y')"),
        {"f": world.feed})
    with pytest.raises(DBAPIError, match="record the pause in feed_status_events first"):
        raw_stage(world, "paused")  # the wrong event
    world.session.execute(text(
        "INSERT INTO feed_status_events (feed_id, action, reason, \"by\") VALUES (:f, 'pause', 'x', 'y')"),
        {"f": world.feed})
    raw_stage(world, "paused")
    assert stage(world) == "paused"
    for to in ("candidate", "rejected"):
        # refused by the stage guard, or earlier by the CHECK that ties registered_at to a live or paused feed
        with pytest.raises(DBAPIError, match="cannot move from paused|ck_feeds_registered_at_matches_stage"):
            raw_stage(world, to)


def test_a_status_event_cannot_be_deleted_or_changed(world):
    pause_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON)
    for statement in ("DELETE FROM feed_status_events", "UPDATE feed_status_events SET reason = 'Changed.'",
                      "TRUNCATE feed_status_events"):
        with pytest.raises(DBAPIError) as refused:
            with world.session.begin_nested():
                world.session.execute(text(statement))
        assert refused.value.orig.sqlstate == "23001", statement


def test_the_event_needs_a_reason_and_a_person(world):
    for reason, by in (("  ", "dev-operator"), ("Synthetic.", " ")):
        with pytest.raises(DBAPIError):
            with world.session.begin_nested():
                world.session.execute(text(
                    "INSERT INTO feed_status_events (feed_id, action, reason, \"by\") VALUES (:f, 'pause', :r, :b)"),
                    {"f": world.feed, "r": reason, "b": by})


# ---------------------------------------------------------------- the daily fetch


def test_a_paused_feed_is_not_fetched_and_a_resumed_one_is_again(world):
    assert world.feed in {f.id for f in FeedRepository(world.session).list_to_fetch()}
    pause_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON)
    assert world.feed not in {f.id for f in FeedRepository(world.session).list_to_fetch()}
    resume_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON)
    assert world.feed in {f.id for f in FeedRepository(world.session).list_to_fetch()}


def test_a_paused_feed_that_a_requirement_selects_is_still_not_fetched(world):
    onboarding_requirement(world.session, world.feed, elements=["volume"])
    pause_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON)
    assert world.feed not in {f.id for f in FeedRepository(world.session).list_to_fetch()}


# ---------------------------------------------------------------- delete


def deliver(world, content: str, n: int = 1):
    key = f"raw/syn_loadable/{n}/f.csv"
    world.blobs.put(key, content.encode())
    return key, run_scheduled(world.session, world.blobs, world.feed, world.live.id, file_ref=key, context=world.context)


def told(world, subject_start: str) -> set[str]:
    return {r.recipient for r in world.session.scalars(
        select(NotificationRow).where(NotificationRow.subject.like(f"{subject_start}%")))}


def test_a_deleted_feed_takes_its_blobs_its_drop_folder_and_its_rows_with_it(world):
    key, finished = deliver(world, volume_csv(10, 0))
    assert finished.run.load.status == "released"
    world.blobs.put("raw/syn_other/1/f.csv", b"x,y\n1,2\n")  # another feed's blob
    folder = world.dropzone / "syn_loadable"
    folder.mkdir(parents=True)
    (folder / "20261120_f.csv").write_bytes(b"a,b\n")
    done = delete_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON, blob_store=world.blobs,
                       dropzone_root=world.dropzone, notifier=world.notifier)
    assert isinstance(done, FeedDeleted) and done.outcome == "deleted" and done.code == "syn_loadable"
    assert done.rows_deleted["feeds"] == 1 and done.rows_deleted["loads"] >= 1
    assert not world.blobs.exists(key) and world.blobs.exists("raw/syn_other/1/f.csv")
    assert not folder.exists() and done.blobs_deleted >= 1 and done.blobs_failed == 0
    with pytest.raises(FeedNotFound):
        FeedRepository(world.session).get(world.feed)
    recorded = world.session.execute(text("SELECT code, \"by\", reason FROM feed_deletions")).one()
    assert tuple(recorded) == ("syn_loadable", "dev-data-owner", REASON)


def test_requirements_that_selected_the_feed_go_back_and_their_requesters_are_told(world):
    requirement = onboarding_requirement(world.session, world.feed, elements=["volume"])
    deliver(world, volume_csv(10, 0))
    assert RequirementRepository(world.session).get(requirement.id).status == "ready"
    product_owner = world.session.execute(text("SELECT owner_id FROM products WHERE feed_id = :f"),
                                          {"f": world.feed}).scalar_one()
    done = delete_feed(world.session, feed_id=world.feed, user=OPERATIONS, reason=REASON, blob_store=world.blobs,
                       dropzone_root=world.dropzone, notifier=world.notifier)
    assert RequirementRepository(world.session).get(requirement.id).status == "no_source_found"
    assert {"dev-requester", product_owner} <= told(world, "Feed deleted")  # the requester and the product's owner
    assert done.requirements_reset == 1 and done.notified >= 2


def test_a_requirement_whose_current_choice_is_another_feed_is_left_alone(world):
    other, other_live = loadable_feed(world.session, code="syn_loadable_two")
    untouched = onboarding_requirement(world.session, other, elements=["volume"])
    delete_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON, blob_store=world.blobs,
                dropzone_root=world.dropzone, notifier=world.notifier)
    assert RequirementRepository(world.session).get(untouched.id).status == "onboarding"
    assert FeedRepository(world.session).get(other).stage == "registered"


def test_a_feed_with_a_load_in_progress_is_not_deleted_and_nothing_goes(world):
    key, _ = deliver(world, volume_csv(10, 0))
    world.session.execute(text(
        "INSERT INTO loads (feed_id, file_ref, purpose, status, go_live_id) VALUES (:f, 'raw/x/y.csv', 'scheduled', "
        "'received', :g)"), {"f": world.feed, "g": world.live.id})
    with pytest.raises(LoadInProgress):
        delete_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON, blob_store=world.blobs,
                    dropzone_root=world.dropzone, notifier=world.notifier)
    assert world.blobs.exists(key) and stage(world) == "registered"  # no blob was touched
    assert world.session.execute(text("SELECT count(*) FROM feed_deletions")).scalar_one() == 0


def test_a_blob_that_cannot_be_deleted_is_counted_and_the_deletion_stands(world):
    key, _ = deliver(world, volume_csv(10, 0))

    class Stubborn(LocalBlobStore):
        def delete(self, key: str) -> None:
            raise OSError("locked")

    done = delete_feed(world.session, feed_id=world.feed, user=OWNER, reason=REASON,
                       blob_store=Stubborn(world.blobs.root),
                       dropzone_root=world.dropzone, notifier=world.notifier)
    assert done.blobs_failed >= 1 and done.blobs_deleted == 0
    with pytest.raises(FeedNotFound):
        FeedRepository(world.session).get(world.feed)
