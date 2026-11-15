"""Step 02 profile (phase 4 plan, assumptions 5 to 13; section 5.4).

Outcomes (CLAUDE.md): profiled; spec_only_low_confidence. The step reads the plan, the caller ends the
transaction, DuckDB runs on the file got through BlobStore.get() with no session, then the feed row is
locked, its stage checked again and the next profile version written.
"""

from __future__ import annotations

import uuid

import pytest

from core.repositories.errors import FeedHasNoFile, FeedNotACandidate, FeedNotFound
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.s02_profile import Profiled, SpecOnlyLowConfidence, profile_plan, record_profile, run_profile
from ports.config import REPO_ROOT
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from ports.storage.local import LocalBlobStore
from tests.onboarded import registered_feed, rejected_feed
from tests.step_chains import end_read, profile_feed

CATALOGUE = SourceCatalogue.load(DEFAULT_CATALOGUE)
FILES = REPO_ROOT / "fixtures" / "sources" / "files"
BACKEND = "dev-backend-team"


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blob")


@pytest.fixture
def lifecycle(db_session, glossary):
    """The candidate feed of syn_instrument_lifecycle, selected by one requirement for issue_date."""
    entry = CATALOGUE.get("syn_instrument_lifecycle")
    feed = FeedRepository(db_session).create_candidate(code=entry.code, name=entry.name, source_code=entry.code,
                                                       connector=entry.connector, source_ref=entry.source_ref)
    requirement = RequirementRepository(db_session).create(requester_id="dev-requester", title="issue date",
                                                          element_keys=["issue_date", "maturity_date"])
    SourceChoiceRepository(db_session).record_known(requirement_id=requirement.id, feed_id=feed.id, source_code=entry.code)
    return feed, requirement


def store(db_session, blobs, feed, content: bytes, name: str = "instrument_lifecycle.csv"):
    key = f"raw/{feed.code}/2026-11-15/{uuid.uuid4()}/{name}"
    blobs.put(key, content)
    return SourceFetchRepository(db_session).record(feed_id=feed.id, outcome="fetched", blob_ref=key,
                                                    size_bytes=max(len(content), 1))


def profile(db_session, blobs, feed):
    return profile_feed(db_session, feed_id=feed.id, catalogue=CATALOGUE, blob_store=blobs, profiled_by=BACKEND)


def test_profiled_holds_columns_types_null_rates_distinct_counts_and_coverage(db_session, blobs, lifecycle):
    feed, requirement = lifecycle
    fetch = store(db_session, blobs, feed, (FILES / "instrument_lifecycle.csv").read_bytes())
    result = profile(db_session, blobs, feed)
    assert isinstance(result, Profiled)
    row = result.profile
    assert (row.version, row.fetch_id, row.outcome, row.row_count, row.profiled_by, row.engine) == (
        1, fetch.id, "profiled", 10, BACKEND, "duckdb 1.5.6")
    assert [(c.name, c.type, c.null_rate, c.distinct_count) for c in row.columns] == [
        ("instr_id", "VARCHAR", 0.0, 10), ("issued_on", "DATE", 0.2, 7), ("state", "VARCHAR", 0.0, 3)]
    [coverage] = row.coverage
    assert coverage.requirement_id == requirement.id
    assert [(i.item, i.column, i.in_file, i.null_rate) for i in coverage.items] == [
        ("issue_date", "issued_on", True, 0.2), ("maturity_date", None, False, None)]
    assert coverage.missing == ["maturity_date"]
    assert row.spec.source_code == "syn_instrument_lifecycle"
    assert FeedProfileRepository(db_session).latest(feed.id) == row


@pytest.mark.parametrize(("content", "name", "detail", "in_file"), [
    # a header read: coverage knows which columns are in the file
    (b"instr_id,issued_on,state\n", "instrument_lifecycle.csv", "The file has a header and no data rows.",
     (True, False)),
    # nothing read: coverage from the spec alone (in_file None)
    (b"\x00\x01\x02\xff\xfe binary", "instrument_lifecycle.csv", "DuckDB could not read the file as a CSV table:",
     (None, None)),
    (b"instr_id,issued_on,state\nsyn-0001,2020-01-01,active\n", "lifecycle.xlsx", "Only CSV files are profiled",
     (None, None)),
    (None, "instrument_lifecycle.csv", "The file is missing from the store:", (None, None)),
])
def test_spec_only_low_confidence_when_the_file_gives_no_data_rows(db_session, blobs, lifecycle, content, name, detail,
                                                                   in_file):
    feed, _ = lifecycle
    fetch = store(db_session, blobs, feed, content or b"x", name)
    if content is None:
        (blobs.root / fetch.blob_ref).unlink()
    result = profile(db_session, blobs, feed)
    assert isinstance(result, SpecOnlyLowConfidence)
    assert result.detail.startswith(detail) and result.profile.detail == result.detail
    assert (result.profile.outcome, result.profile.row_count) == ("spec_only_low_confidence", 0)
    [coverage] = result.profile.coverage
    assert [(i.item, i.declared, i.in_file) for i in coverage.items] == [
        ("issue_date", True, in_file[0]), ("maturity_date", False, in_file[1])]
    assert coverage.missing == ["maturity_date"]


def test_a_header_only_file_keeps_its_column_names(db_session, blobs, lifecycle):
    feed, _ = lifecycle
    store(db_session, blobs, feed, b"instr_id,issued_on,state\n")
    result = profile(db_session, blobs, feed)
    assert [c.name for c in result.profile.columns] == ["instr_id", "issued_on", "state"]


def test_the_profile_reads_the_feeds_latest_fetched_file(db_session, blobs, lifecycle):
    feed, _ = lifecycle
    store(db_session, blobs, feed, b"instr_id,issued_on,state\n")
    latest = store(db_session, blobs, feed, (FILES / "instrument_lifecycle.csv").read_bytes())
    SourceFetchRepository(db_session).record(feed_id=feed.id, outcome="source_down", detail="no answer")
    result = profile(db_session, blobs, feed)
    assert (result.outcome, result.profile.fetch_id) == ("profiled", latest.id)


def test_each_profile_is_the_next_version(db_session, blobs, lifecycle):
    feed, _ = lifecycle
    store(db_session, blobs, feed, (FILES / "instrument_lifecycle.csv").read_bytes())
    assert [profile(db_session, blobs, feed).profile.version for _ in range(2)] == [1, 2]


def test_the_profile_uses_only_blob_store_get(db_session, blobs, lifecycle):
    feed, _ = lifecycle
    store(db_session, blobs, feed, (FILES / "instrument_lifecycle.csv").read_bytes())
    calls = []

    class OnlyGet:
        name = "spy"

        def get(self, key):
            calls.append(("get", key))
            return blobs.get(key)

        def __getattr__(self, attr):  # put, exists, list_keys: any other use fails the test
            raise AssertionError(f"step 02 used BlobStore.{attr}")

    profile_feed(db_session, feed_id=feed.id, catalogue=CATALOGUE, blob_store=OnlyGet(), profiled_by=BACKEND)
    assert [c[0] for c in calls] == ["get"]


def test_the_temporary_file_is_removed_after_a_profile_and_after_a_failure(db_session, blobs, lifecycle, tmp_path,
                                                                           monkeypatch):
    import tempfile

    import core.steps.s02_profile as step

    feed, _ = lifecycle
    store(db_session, blobs, feed, (FILES / "instrument_lifecycle.csv").read_bytes())
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    plan = profile_plan(db_session, feed_id=feed.id, catalogue=CATALOGUE)
    end_read(db_session)
    run_profile(plan, blob_store=blobs)
    assert list(scratch.iterdir()) == []

    def boom(path):
        assert path.is_file()
        raise RuntimeError("synthetic failure inside DuckDB")

    monkeypatch.setattr(step, "profile_csv", boom)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        run_profile(plan, blob_store=blobs)
    assert list(scratch.iterdir()) == []


def test_only_a_candidate_with_a_fetched_file_is_profiled(db_session, blobs, lifecycle):
    feed, _ = lifecycle
    with pytest.raises(FeedHasNoFile):
        profile_plan(db_session, feed_id=feed.id, catalogue=CATALOGUE)
    SourceFetchRepository(db_session).record(feed_id=feed.id, outcome="source_down", detail="no answer")
    with pytest.raises(FeedHasNoFile):
        profile_plan(db_session, feed_id=feed.id, catalogue=CATALOGUE)
    with pytest.raises(FeedNotFound):
        profile_plan(db_session, feed_id=uuid.uuid4(), catalogue=CATALOGUE)
    other = FeedRepository(db_session).create_candidate(code="syn_listing_details", name="Listing",
                                                        source_code="syn_listing_details", connector="local_file",
                                                        source_ref="files/listing_details.csv")
    registered_feed(db_session, other.id)
    with pytest.raises(FeedNotACandidate):
        profile_plan(db_session, feed_id=other.id, catalogue=CATALOGUE)


def test_a_feed_rejected_during_the_run_writes_no_profile(db_session, blobs, lifecycle):
    feed, _ = lifecycle
    store(db_session, blobs, feed, (FILES / "instrument_lifecycle.csv").read_bytes())
    plan = profile_plan(db_session, feed_id=feed.id, catalogue=CATALOGUE)
    end_read(db_session)
    attempt = run_profile(plan, blob_store=blobs)
    rejected_feed(db_session, feed.id)  # a rejection committed while DuckDB ran
    before = len(FeedProfileRepository(db_session).history(feed.id))
    with pytest.raises(FeedNotACandidate):
        record_profile(db_session, attempt, profiled_by=BACKEND)
    assert len(FeedProfileRepository(db_session).history(feed.id)) == before


def test_a_source_that_left_the_catalogue_cannot_be_profiled(db_session, blobs, lifecycle):
    from ports.connectors.catalogue import UnknownSource

    feed, _ = lifecycle
    store(db_session, blobs, feed, (FILES / "instrument_lifecycle.csv").read_bytes())
    with pytest.raises(UnknownSource):
        profile_plan(db_session, feed_id=feed.id, catalogue=SourceCatalogue([]))
