"""Phase 7.16 T2: the step that erases profiling history, what the feed page shows of it, and the audit rows
(plan docs/plans/phase-7.16.md, 2.4). The database function is tested in tests/test_db_profile_erasure.py."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from core.repositories.errors import FeedNotFound, NotOwnerOrOperations
from core.repositories.feeds import FeedRepository
from core.steps.audit import CSV_COLUMNS, audit_csv, audit_rows
from core.steps.errors import ReasonNeeded, UnknownProfileVersion
from core.steps.feed_page import feed_page
from core.steps.profile_erase import NothingToErase, ProfilesErased, erase_profiles
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from tests.conftest import dev_user_for
from tests.onboarded import profiled, registered_feed

MONTH = f"{datetime.now(UTC):%Y-%m}"
OWNER = dev_user_for("data_owner")
OPERATOR = dev_user_for("operations")
STEWARD = dev_user_for("data_steward")
WHY = "The old profiles of the first file are not needed."


@pytest.fixture
def feed(db_session, glossary):
    """A registered feed (profile 1: its registration's) and three more profiles: 2 and 3 erasable, 4 the latest."""
    row = FeedRepository(db_session).create_candidate(code="syn_erase", name="Erase", source_code="syn_erase",
                                                      connector="local_file", source_ref="files/syn_erase.csv")
    registered_feed(db_session, row.id)
    for _ in range(3):
        profiled(db_session, row.id)
    return FeedRepository(db_session).get(row.id)


def test_the_owner_erases_older_profiles_and_gets_what_went_and_what_stayed(db_session, feed):
    done = erase_profiles(db_session, feed_id=feed.id, user=OWNER, before_version=4, reason=WHY)
    assert isinstance(done, ProfilesErased) and done.erased_versions == [2, 3]
    assert [(k.version, k.reason) for k in done.kept] == [(1, "registration")]  # version 4 is not before itself


def test_operations_may_erase_for_any_feed(db_session, feed):
    assert isinstance(erase_profiles(db_session, feed_id=feed.id, user=OPERATOR, before_version=4, reason=WHY), ProfilesErased)


def test_nothing_to_erase_is_an_outcome_with_its_reasons_and_writes_nothing(db_session, feed):
    done = erase_profiles(db_session, feed_id=feed.id, user=OWNER, before_version=2, reason=WHY)  # only profile 1, kept
    assert isinstance(done, NothingToErase) and [(k.version, k.reason) for k in done.kept] == [(1, "registration")]
    assert db_session.scalar(text("SELECT count(*) FROM profile_erasures")) == 0
    assert db_session.scalar(text("SELECT count(*) FROM feed_profiles WHERE feed_id = :f"), {"f": feed.id}) == 4


@pytest.mark.parametrize("kwargs, error", [
    ({"user": STEWARD}, NotOwnerOrOperations),  # a backend person who is neither the owner nor operations
    ({"reason": "  "}, ReasonNeeded), ({"reason": "x" * 501}, ReasonNeeded),
    ({"before_version": 99}, UnknownProfileVersion), ({"before_version": 0}, UnknownProfileVersion)])
def test_named_refusals_and_nothing_is_erased(db_session, feed, kwargs, error):
    args = {"user": OWNER, "before_version": 4, "reason": WHY, **kwargs}
    with pytest.raises(error):
        erase_profiles(db_session, feed_id=feed.id, **args)
    assert db_session.scalar(text("SELECT count(*) FROM feed_profiles WHERE feed_id = :f"), {"f": feed.id}) == 4


def test_an_unknown_feed_is_not_found(db_session):
    with pytest.raises(FeedNotFound):
        erase_profiles(db_session, feed_id=uuid.uuid4(), user=OPERATOR, before_version=2, reason=WHY)


def test_the_feed_page_says_why_a_profile_stays_and_who_erased_the_others(db_session, feed):
    catalogue = SourceCatalogue.load(DEFAULT_CATALOGUE)
    before = feed_page(db_session, feed, catalogue)
    assert [(p.version, p.keep_reason) for p in before.profiles] == [(1, "registration"), (2, None), (3, None), (4, "latest")]
    assert before.erasures == []
    erase_profiles(db_session, feed_id=feed.id, user=OWNER, before_version=4, reason=WHY)
    after = feed_page(db_session, feed, catalogue)
    assert [(p.version, p.keep_reason) for p in after.profiles] == [(1, "registration"), (4, "latest")]
    [erased] = after.erasures
    assert (erased.erased_versions, erased.by, erased.reason) == ([2, 3], OWNER.id, WHY)


def test_an_erase_is_a_row_of_the_audit_month_with_who_why_and_which_versions(db_session, feed):
    erase_profiles(db_session, feed_id=feed.id, user=OWNER, before_version=4, reason=WHY)
    [row] = [r for r in audit_rows(db_session, month=MONTH) if r.kind == "erasure"]
    assert (row.who, row.decision, row.item, row.by, row.feed_code, row.note) == (
        OWNER.id, "erased", "profiles", "manual", "syn_erase", WHY)
    assert "2, 3" in row.summary and row.feed_id == feed.id and row.waiver is False
    assert audit_rows(db_session, month=MONTH, person="dev-operator") == [r for r in audit_rows(db_session, month=MONTH, person="dev-operator")
                                                                       if r.kind != "erasure"]  # the person filter applies
    assert [r.kind for r in audit_rows(db_session, month=MONTH, feed_id=feed.id) if r.kind == "erasure"] == ["erasure"]
    assert not [r for r in audit_rows(db_session, month="2000-01") if r.kind == "erasure"]  # month bounds


def test_the_csv_carries_the_erase_and_neutralises_a_formula_in_its_reason(db_session, feed):
    erase_profiles(db_session, feed_id=feed.id, user=OWNER, before_version=4, reason="=HYPERLINK(\"http://x\")")
    lines = list(csv.reader(io.StringIO(audit_csv(audit_rows(db_session, month=MONTH)))))
    header = lines[0]
    rows = [dict(zip(header, line)) for line in lines[1:]]
    assert tuple(header) == CSV_COLUMNS
    [row] = [r for r in rows if r["kind"] == "erasure"]
    assert (row["who"], row["decision"], row["item"], row["note"]) == (OWNER.id, "erased", "profiles", "'=HYPERLINK(\"http://x\")")
