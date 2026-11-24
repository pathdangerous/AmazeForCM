"""Migration 0036 (phase 7.16 T1; plan docs/plans/phase-7.16.md, 2.3): amaze_erase_profiles() is the only path that deletes
a feed_profiles row; it keeps the latest profile and every profile something points to, and writes profile_erasures.
By raw SQL on the test's session; each refusal is checked by its message."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.domain.profiling import FeedProfile
from core.repositories.feeds import FeedRepository
from tests.onboarded import profiled, registered_feed, signed_off

ERASE = "SELECT amaze_erase_profiles(:f, :before, :reason, :by)"


def future() -> datetime:
    return datetime.now(UTC) + timedelta(days=1)


def erase(session, feed_id, *, before=None, reason="Old profiles of a file nobody uses.", by="dev-operator") -> dict:
    return session.execute(text(ERASE), {"f": feed_id, "before": before or future(), "reason": reason, "by": by}).scalar_one()


def refused(session, feed_id, match: str, **over):
    with pytest.raises(DBAPIError, match=match):
        with session.begin_nested():
            erase(session, feed_id, **over)


def versions(session, feed_id) -> list[int]:
    return list(session.scalars(text("SELECT version FROM feed_profiles WHERE feed_id = :f ORDER BY version"), {"f": feed_id}))


def kept_of(done: dict) -> dict[int, str]:
    return {k["version"]: k["reason"] for k in done["kept"]}


@pytest.fixture
def feed(db_session, glossary):
    """A registered feed: profile 1 is the one its registration's sign-off is of."""
    row = FeedRepository(db_session).create_candidate(code="syn_erase", name="Erase", source_code="syn_erase",
                                                      connector="local_file", source_ref="files/syn_erase.csv")
    registered_feed(db_session, row.id)
    return row.id


def test_it_erases_what_nothing_points_to_and_keeps_the_latest_and_the_registered(db_session, feed):
    plain = [profiled(db_session, feed), profiled(db_session, feed)]  # versions 2 and 3
    latest = profiled(db_session, feed)  # version 4
    done = erase(db_session, feed)
    assert done["erased_versions"] == [2, 3] and done["erased_count"] == 2
    assert kept_of(done) == {1: "registration", 4: "latest"}
    assert versions(db_session, feed) == [1, 4]
    record = db_session.execute(text("SELECT * FROM profile_erasures WHERE feed_id = :f"), {"f": feed}).mappings().one()
    assert (record["by"], record["reason"], record["erased_count"], record["kept_count"], record["feed_code"]) == (
        "dev-operator", "Old profiles of a file nobody uses.", 2, 2, "syn_erase")
    assert sorted(map(str, record["erased_ids"])) == sorted(str(p.id) for p in plain) and record["erased_versions"] == [2, 3]
    assert str(done["erasure_id"]) == str(record["id"]) and latest.version == 4
    assert [k["id"] for k in record["kept"]] and record["at"] is not None


def test_only_profiles_before_the_cutoff_go(db_session, feed):
    profiled(db_session, feed)
    third = profiled(db_session, feed)
    profiled(db_session, feed)
    done = erase(db_session, feed, before=third.created_at)  # "before" is strict: the third is not before itself
    assert done["erased_versions"] == [2] and versions(db_session, feed) == [1, 3, 4]


def pointed_to_by_a_glossary_run(session, feed_id, profile: FeedProfile):
    from core.repositories.glossary_runs import GlossaryRunRepository

    GlossaryRunRepository(session).queue(feed_id=feed_id, profile_id=profile.id, reason="requested",
                                         requested_by="dev-glossary-owner")


def pointed_to_by_an_arrival(session, feed_id, profile: FeedProfile):
    import hashlib

    from core.repositories.go_lives import GoLiveRepository
    from core.repositories.loads import LoadRepository
    from tests.configured import approved_mapping, go_live

    approved_mapping(session, feed_id, "qty", "volume")
    live = go_live(session, feed_id)
    repo = LoadRepository(session)
    load = repo.create(feed_id=feed_id, file_ref="raw/x.csv", purpose="scheduled", go_live_id=GoLiveRepository(session).latest(feed_id).id)
    repo.record_arrival(load.id, outcome="additive_parked", detail={"new_columns": ["x"]}, profile_id=profile.id,
                        content_sha256=hashlib.sha256(load.id.bytes).hexdigest())
    assert live is not None


def pointed_to_by_a_report(session, feed_id, profile: FeedProfile):
    session.execute(text(
        "INSERT INTO reports (kind, subject_id, subject_role, period_start, period_end, version, numbers, numbers_sha256, "
        "narrative_status, generated_by) VALUES ('daily', 'dev-data-owner', 'data_owner', '2026-11-02', '2026-11-03', 1, "
        "CAST(:n AS jsonb), 'x', 'none', 'job')"), {"n": json.dumps({"mentions": str(profile.id)})})


def pointed_to_by_a_code_version(session, feed_id, profile: FeedProfile):
    from core.engine.codegen import sha256_of
    from core.repositories.code_versions import CodeVersionRepository

    files = {"README.md": f"Made from profile {profile.id}."}
    CodeVersionRepository(session).add_version(feed_id=feed_id, files=files, sha256=sha256_of(files), mapping_ids=[],
                                               rule_ids=[], transformation_ids=[], test_load_id=None, test_load_result={},
                                               approved_by="dev-operator")


@pytest.mark.parametrize("pointer, reason", [
    (pointed_to_by_a_glossary_run, "glossary_run"), (pointed_to_by_an_arrival, "load_arrival"),
    (pointed_to_by_a_report, "report"), (pointed_to_by_a_code_version, "code_version")])
def test_a_profile_something_points_to_is_kept(db_session, feed, pointer, reason):
    pointed = profiled(db_session, feed)  # version 2
    pointer(db_session, feed, pointed)
    erasable = profiled(db_session, feed)  # version 3
    profiled(db_session, feed)  # version 4, the latest
    done = erase(db_session, feed)
    assert done["erased_versions"] == [erasable.version] and kept_of(done) == {1: "registration", 2: reason, 4: "latest"}
    assert versions(db_session, feed) == [1, 2, 4]


def test_a_signed_off_profile_is_kept_even_without_a_registration(db_session, glossary):
    row = FeedRepository(db_session).create_candidate(code="syn_signed", name="Signed", source_code="syn_signed",
                                                      connector="local_file", source_ref="files/syn_signed.csv")
    signed_off(db_session, row.id)  # profile 1, a sign-off, no registration
    profiled(db_session, row.id)
    profiled(db_session, row.id)
    done = erase(db_session, row.id)
    assert done["erased_versions"] == [2] and kept_of(done) == {1: "signoff", 3: "latest"}


def test_when_every_profile_before_the_cutoff_is_kept_nothing_is_erased_and_nothing_is_recorded(db_session, feed):
    profiled(db_session, feed)  # version 2, the latest
    refused(db_session, feed, "nothing to erase")
    refused(db_session, feed, "nothing to erase", before=datetime(2000, 1, 1, tzinfo=UTC))
    assert versions(db_session, feed) == [1, 2]
    assert db_session.scalar(text("SELECT count(*) FROM profile_erasures")) == 0


def test_a_reason_a_person_and_a_feed_are_needed(db_session, feed):
    profiled(db_session, feed)
    profiled(db_session, feed)
    refused(db_session, feed, "needs a reason", reason="  ")
    refused(db_session, feed, "needs the person", by="")
    import uuid

    refused(db_session, uuid.uuid4(), "does not exist")
    assert versions(db_session, feed) == [1, 2, 3] and db_session.scalar(text("SELECT count(*) FROM profile_erasures")) == 0


def test_nothing_else_deletes_a_profile(db_session, feed):
    for _ in range(2):
        profiled(db_session, feed)
    for sql in ("DELETE FROM feed_profiles WHERE feed_id = :f", "DELETE FROM feed_profiles WHERE version = 2 AND feed_id = :f"):
        with pytest.raises(DBAPIError, match="append-only"):
            with db_session.begin_nested():
                db_session.execute(text(sql), {"f": feed})
    with pytest.raises(DBAPIError, match="append-only|truncate"):
        with db_session.begin_nested():
            db_session.execute(text("TRUNCATE feed_profiles"))
    erase(db_session, feed)  # the function opens its window only for the rows it decided on, and closes it
    for sql in ("DELETE FROM feed_profiles WHERE feed_id = :f", "DELETE FROM feed_signoffs WHERE feed_id = :f",
                "DELETE FROM profile_erasures WHERE feed_id = :f", "DELETE FROM feed_registrations WHERE feed_id = :f"):
        with pytest.raises(DBAPIError, match="append-only|restrict|refused"):
            with db_session.begin_nested():
                db_session.execute(text(sql), {"f": feed})
    assert versions(db_session, feed) == [1, 3]  # profile 2 went through the function; nothing else could be deleted


def test_the_record_is_append_only_and_its_counts_must_match(db_session, feed):
    profiled(db_session, feed)
    profiled(db_session, feed)
    done = erase(db_session, feed)
    for sql in ("UPDATE profile_erasures SET reason = 'x'", "DELETE FROM profile_erasures"):
        with pytest.raises(DBAPIError, match="append-only"):
            with db_session.begin_nested():
                db_session.execute(text(sql))
    with pytest.raises(DBAPIError, match="append-only|truncate"):
        with db_session.begin_nested():
            db_session.execute(text("TRUNCATE profile_erasures"))
    with pytest.raises(DBAPIError, match="erased_count"):
        with db_session.begin_nested():
            db_session.execute(text(
                "INSERT INTO profile_erasures (feed_id, feed_code, before, \"by\", reason, erased_count, erased_ids, "
                "erased_versions, kept, kept_count) VALUES (:f, 'x', now(), 'p', 'r', 3, ARRAY[gen_random_uuid()], "
                "ARRAY[2], '[]', 0)"), {"f": feed})
    assert done["erased_count"] == 1


def test_the_next_profile_still_takes_the_next_version_and_the_record_outlives_the_feed(db_session, feed):
    profiled(db_session, feed)
    profiled(db_session, feed)
    erase(db_session, feed)
    assert profiled(db_session, feed).version == 4  # the latest was kept, so the sequence is intact
    db_session.execute(text("SELECT amaze_delete_feed(:f, 'dev-operator', 'The feed is not needed.')"), {"f": feed})
    assert db_session.scalar(text("SELECT count(*) FROM profile_erasures WHERE feed_id = :f"), {"f": feed}) == 1
