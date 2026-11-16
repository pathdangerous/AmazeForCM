"""GlossaryRunRepository (phase 5 plan, assumptions 10 to 12 and 19): queue, claim with a lease, finish, fail.

One session (the test's transaction); the races through the app are in test_concurrency.py (task 11).
"""

from __future__ import annotations

import pytest

from core.repositories.errors import RunSuperseded
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.glossary_runs import GlossaryRunRepository
from tests.configured import a_registered_feed

GLENN = "dev-glossary-owner"


@pytest.fixture
def runs(db_session):
    return GlossaryRunRepository(db_session)


@pytest.fixture
def feed(db_session):
    feed_id = a_registered_feed(db_session, "syn_runs")
    return feed_id, FeedProfileRepository(db_session).latest(feed_id).id


def queue(runs, feed, **more):
    feed_id, profile_id = feed
    fields = {"reason": "requested", "requested_by": GLENN, **more}
    return runs.queue(feed_id=feed_id, profile_id=profile_id, **fields)


PASSED = 0  # a lease of 0 seconds has passed by the next statement (clock_timestamp moves on)


def test_a_queued_run_waits_at_attempt_0(runs, feed):
    run, created = queue(runs, feed)
    assert created and (run.status, run.attempt, run.lease_until, run.columns) == ("queued", 0, None, [])


def test_asking_again_while_one_is_active_returns_it(runs, feed):
    first, _ = queue(runs, feed)
    again, created = queue(runs, feed, reason="registered")
    assert (again.id, created) == (first.id, False)
    runs.claim(lease_seconds=60)
    while_running, created = queue(runs, feed)
    assert (while_running.id, created) == (first.id, False)


def test_a_sent_back_column_joins_a_queued_run(runs, feed):
    first, _ = queue(runs, feed, reason="sent_back", columns=["maturity"])
    again, _ = queue(runs, feed, reason="sent_back", columns=["maturity", "lot"])
    assert (again.id, again.columns) == (first.id, ["maturity", "lot"])


def test_a_finished_run_lets_a_new_one_queue(runs, feed):
    first, _ = queue(runs, feed)
    runs.claim(lease_seconds=60)
    runs.finish(first.id, results={}, proposal_log_id=None)
    second, created = queue(runs, feed)
    assert created and second.id != first.id
    assert [r.status for r in runs.list_for_feed(feed[0])] == ["done", "queued"]
    assert runs.latest(feed[0]).id == second.id


def test_claim_takes_the_oldest_queued_run_with_a_lease(runs, feed, db_session):
    other = (a_registered_feed(db_session, "syn_runs_2"),)
    first, _ = queue(runs, feed)
    queue(runs, (other[0], FeedProfileRepository(db_session).latest(other[0]).id))
    claimed = runs.claim(lease_seconds=60)
    assert (claimed.id, claimed.status, claimed.attempt) == (first.id, "running", 1)
    assert claimed.lease_until is not None and claimed.started_at is not None


def test_claim_takes_nothing_when_nothing_waits(runs, feed):
    assert runs.claim(lease_seconds=60) is None
    queue(runs, feed)
    runs.claim(lease_seconds=60)
    assert runs.claim(lease_seconds=60) is None  # running, lease not passed


def test_a_running_run_whose_lease_passed_is_claimed_again_on_the_next_attempt(runs, feed, db_session):
    run, _ = queue(runs, feed)
    runs.claim(lease_seconds=PASSED)
    again = runs.claim(lease_seconds=60)
    assert (again.id, again.attempt) == (run.id, 2)


def test_a_fourth_attempt_is_not_made(runs, feed, db_session):
    run, _ = queue(runs, feed)
    for _ in range(3):
        runs.claim(lease_seconds=PASSED)
    assert runs.claim(lease_seconds=60) is None
    gave_up = runs.get(run.id)
    assert (gave_up.status, gave_up.attempt, gave_up.error) == ("failed", 3, "gave up after 3 attempts")


def test_lock_is_refused_unless_still_running_on_this_attempt(runs, feed, db_session):
    run, _ = queue(runs, feed)
    runs.claim(lease_seconds=PASSED)
    assert runs.lock(run.id, attempt=1).status == "running"
    runs.claim(lease_seconds=60)
    with pytest.raises(RunSuperseded):
        runs.lock(run.id, attempt=1)


def test_finish_and_fail(runs, feed):
    run, _ = queue(runs, feed)
    runs.claim(lease_seconds=60)
    done = runs.finish(run.id, results={"score_year": "proposed"}, proposal_log_id=None)
    assert (done.status, done.results, done.finished_at is not None) == ("done", {"score_year": "proposed"}, True)
    second, _ = queue(runs, feed)
    runs.claim(lease_seconds=60)
    failed = runs.fail(second.id, error="The model is not available.")
    assert (failed.status, failed.error) == ("failed", "The model is not available.")
