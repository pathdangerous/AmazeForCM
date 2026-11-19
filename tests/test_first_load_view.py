"""The first-load panel's facts, message and timeline come from the feed's LATEST scheduled load (phase 7.8 follow-up,
item 2). Deterministic: from the database only, no model call. Before this, the facts read the oldest scheduled load
while the message told about the newest, so after a second delivery the panel contradicted itself."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.repositories.loads import LoadRepository
from core.steps.first_load_view import first_load_facts, latest_load_summary, load_timeline
from core.steps.s12_arrival import Recipients
from core.steps.s20_create_product import FinishContext
from ports.notify.inapp import InAppNotifier
from ports.storage.local import LocalBlobStore
from tests.configured import loadable_feed, volume_csv
from tests.step_chains import arrive, run_scheduled


@pytest.fixture
def world(db_session, glossary, tmp_path):
    feed, live = loadable_feed(db_session)
    blobs = LocalBlobStore(tmp_path / "blob")
    context = FinishContext(notifier=InAppNotifier(db_session), threshold=Decimal("90"), operations=["dev-operations"],
                            backend_team=["dev-backend-team"])
    return SimpleNamespace(feed=feed, live=live, session=db_session, blobs=blobs, context=context, n=0)


def deliver(world, content: str):
    world.n += 1
    key = f"raw/syn_loadable/{world.n}/f.csv"
    world.blobs.put(key, content.encode())
    return run_scheduled(world.session, world.blobs, world.feed, world.live.id, file_ref=key, context=world.context)


def resend(world, content: str):
    """The same bytes again: step 12 quarantines the new scheduled load (it never reaches the engine)."""
    world.n += 1
    key = f"raw/syn_loadable/{world.n}/f.csv"
    world.blobs.put(key, content.encode())
    load = LoadRepository(world.session).create(feed_id=world.feed, file_ref=key, purpose="scheduled",
                                                 go_live_id=world.live.id)
    arrive(world.session, blob_store=world.blobs, load_id=load.id, notifier=InAppNotifier(world.session),
           recipients=Recipients(glossary_owners=[], stewards=[], backend_team=["dev-backend-team"],
                                 operations=["dev-operations"]))
    return LoadRepository(world.session).get(load.id)


def facts_of(world) -> dict[str, str]:
    return {f.label: f.value for f in first_load_facts(world.session, world.feed)}


def test_no_scheduled_load_has_no_facts_no_message_and_no_timeline(world):
    assert first_load_facts(world.session, world.feed) == []
    assert latest_load_summary(world.session, world.feed) is None
    assert load_timeline(world.session, world.feed) == []


def test_the_facts_are_the_latest_loads_not_the_oldest(world):
    deliver(world, volume_csv(10, 5))  # held: 50.0
    deliver(world, volume_csv(10, 0))  # released: 100.0
    facts = facts_of(world)
    assert (facts["Load"], facts["Outcome"], facts["Trust score"], facts["Rows flagged"]) == (
        "released", "released", "100.0", "0")


def test_a_held_load_after_a_released_one_shows_the_held_one(world):
    deliver(world, volume_csv(10, 0))
    deliver(world, volume_csv(10, 4))  # 60.0, held
    facts = facts_of(world)
    assert (facts["Load"], facts["Trust score"], facts["Rows flagged"]) == ("held", "60.0", "4")
    assert "held" in latest_load_summary(world.session, world.feed)


def test_the_message_describes_the_same_load_as_the_facts(world):
    deliver(world, volume_csv(10, 5))
    deliver(world, volume_csv(10, 2))  # 80.0, held, the latest
    facts, text = facts_of(world), latest_load_summary(world.session, world.feed)
    assert facts["Trust score"] == "80.0" and facts["Rows flagged"] == "2"
    assert "80.0" in text and "threshold 90.0" in text and "2 of 10" in text and "50.0" not in text
    assert "consumers were warned" in text


def test_a_released_load_says_so(world):
    deliver(world, volume_csv(10, 0))
    assert "released" in latest_load_summary(world.session, world.feed)


def test_a_file_that_was_loaded_before_is_quarantined_and_the_message_says_so(world):
    deliver(world, volume_csv(10, 0))
    assert resend(world, volume_csv(10, 0)).status == "quarantined"  # byte for byte the same
    assert facts_of(world)["Load"] == "quarantined"
    assert "same file" in latest_load_summary(world.session, world.feed)


def test_a_file_that_fails_is_rolled_back_and_the_message_names_the_stage(world):
    deliver(world, "instr_id,trade_date,traded_qty\nsyn-1,not-a-date,5\n")
    text = latest_load_summary(world.session, world.feed)
    assert facts_of(world)["Load"] == "failed rolled back"
    assert "rolled back" in text and ("curated" in text or "stage" in text)


def test_every_scheduled_load_is_a_timeline_line_in_time_order(world):
    deliver(world, volume_csv(10, 5))
    deliver(world, volume_csv(10, 0))
    resend(world, volume_csv(10, 0))
    entries = load_timeline(world.session, world.feed)
    assert [e.title for e in entries] == ["load held", "load released", "load quarantined"]
    assert [e.at for e in entries] == sorted(e.at for e in entries)
    assert all(e.step == "first_load" and e.kind == "event" and e.actor == "system" for e in entries)
    held = entries[0].detail
    assert (held["rows"], held["flagged_rows"], held["trust_score"]) == (10, 5, "50.0")
