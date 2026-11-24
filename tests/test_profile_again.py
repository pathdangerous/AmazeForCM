"""Phase 7.16 follow-up: "Profile again" runs step 02 on a registered feed's latest file as a new profile version, writes no
sign-off, and only when the file has the feed's current columns (a changed structure goes through step 12, never here). Its
extra version is what the recorded erase can then remove, with every keep rule unchanged."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from core.domain.profiling import FileColumn
from core.repositories.errors import FeedNotACandidate, FeedNotRegistered
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.profile_again import ProfileDiffers, ProfiledAgain, again_plan, profile_again
from core.steps.profile_erase import ProfilesErased, erase_profiles
from core.steps.s02_profile import run_profile
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from ports.storage.local import LocalBlobStore
from tests.conftest import dev_user_for
from tests.onboarded import registered_feed

CATALOGUE = SourceCatalogue.load(DEFAULT_CATALOGUE)
CSV = "instr_id,trade_date,traded_qty\nsyn-0001,2026-11-02,120500\n"
OPERATOR = dev_user_for("operations")


@pytest.fixture
def world(db_session, glossary, tmp_path):
    feed = FeedRepository(db_session).create_candidate(
        code="syn_exchange_volumes", name="Volumes", source_code="syn_exchange_volumes", connector="local_file",
        source_ref="files/exchange_volumes.csv")
    registered_feed(db_session, feed.id, columns=[
        FileColumn(name=n, type="VARCHAR", null_count=0, null_rate=0.0, distinct_count=1)
        for n in ("instr_id", "trade_date", "traded_qty")])
    blobs = LocalBlobStore(tmp_path / "blob")
    return feed.id, blobs


def deliver(session, blobs, feed_id, content: str = CSV, n: int = 1):
    key = f"raw/syn_exchange_volumes/{n}/volumes.csv"
    blobs.put(key, content.encode())
    SourceFetchRepository(session).record(feed_id=feed_id, outcome="fetched", blob_ref=key, size_bytes=len(content))


def again(session, blobs, feed_id):
    plan = again_plan(session, feed_id=feed_id, catalogue=CATALOGUE)
    return profile_again(session, run_profile(plan, blob_store=blobs), profiled_by="dev-backend-team")


def versions(session, feed_id):
    return [p.version for p in FeedProfileRepository(session).history(feed_id)]


def test_profile_again_is_a_new_version_with_no_sign_off(db_session, world):
    feed_id, blobs = world
    deliver(db_session, blobs, feed_id)
    signoffs = db_session.scalar(text("SELECT count(*) FROM feed_signoffs"))
    done = again(db_session, blobs, feed_id)
    assert isinstance(done, ProfiledAgain) and done.profile.version == 2 and versions(db_session, feed_id) == [1, 2]
    assert db_session.scalar(text("SELECT count(*) FROM feed_signoffs")) == signoffs  # no sign-off, no summary
    assert FeedProfileRepository(db_session).latest(feed_id).id == done.profile.id


def test_the_extra_version_is_erasable_and_every_keep_rule_still_holds(db_session, world):
    feed_id, blobs = world
    deliver(db_session, blobs, feed_id)
    again(db_session, blobs, feed_id)  # version 2
    again(db_session, blobs, feed_id)  # version 3, the latest
    done = erase_profiles(db_session, feed_id=feed_id, user=OPERATOR, before_version=3, reason="Profiled twice by mistake.")
    assert isinstance(done, ProfilesErased) and done.erased_versions == [2]
    assert [(k.version, k.reason) for k in done.kept] == [(1, "registration")]
    assert versions(db_session, feed_id) == [1, 3]


def test_a_file_whose_columns_differ_is_a_named_outcome_and_writes_nothing(db_session, world):
    feed_id, blobs = world
    deliver(db_session, blobs, feed_id, "instr_id,trade_date,traded_qty,extra\nsyn-0001,2026-11-02,1,x\n")
    done = again(db_session, blobs, feed_id)
    assert isinstance(done, ProfileDiffers) and done.added == ["extra"] and done.missing == []
    deliver(db_session, blobs, feed_id, "instr_id,trade_date\nsyn-0001,2026-11-02\n", n=2)
    assert again(db_session, blobs, feed_id).missing == ["traded_qty"]
    assert versions(db_session, feed_id) == [1]


def test_only_a_registered_feed_is_profiled_again(db_session, world, glossary):
    candidate = FeedRepository(db_session).create_candidate(
        code="syn_other", name="Other", source_code="syn_other", connector="local_file", source_ref="files/x.csv")
    with pytest.raises(FeedNotRegistered):
        again_plan(db_session, feed_id=candidate.id, catalogue=CATALOGUE)
    assert FeedNotACandidate is not FeedNotRegistered  # a candidate is profiled by step 02 as before, never here
