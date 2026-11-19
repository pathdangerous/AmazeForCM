"""OnboardingRepository and OnboardingJobRepository (phase 7.8 plan, assumptions 31 to 33, 38; migration 0024)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from core.domain.onboarding import MAX_JOB_ATTEMPTS, ONBOARDING_STEPS
from core.repositories.errors import JobSuperseded, OnboardingAlreadyExists, OnboardingNotFound
from core.repositories.feeds import FeedRepository
from core.repositories.onboarding import OnboardingJobRepository, OnboardingRepository


@pytest.fixture
def s(db_session):
    return db_session


@pytest.fixture
def feed(s):
    return FeedRepository(s).create_candidate(code="syn_onboarding_repo", name="Repo").id


@pytest.fixture
def sessions(s):
    return OnboardingRepository(s)


@pytest.fixture
def jobs(s):
    return OnboardingJobRepository(s)


@pytest.fixture
def session(sessions, feed):
    return sessions.create(feed_id=feed, created_by="dev-operator")


# ---------------------------------------------------------------- sessions

def test_the_steps_are_the_nine_in_order():
    assert ONBOARDING_STEPS == ("upload", "profile", "glossary", "mapping", "transformations", "rules", "go_live",
                                "first_load", "product_ready")


def test_a_new_session_has_uploaded_and_stands_at_profile(session, feed):
    assert (session.feed_id, session.created_by, session.status, session.current_step, session.model_calls) == \
        (feed, "dev-operator", "active", "profile", 0)
    assert session.step_states == {"upload": "done", **{step: "waiting" for step in ONBOARDING_STEPS[1:]}}
    assert session.created_at <= session.updated_at


def test_a_feed_has_one_session(sessions, session, feed):
    with pytest.raises(OnboardingAlreadyExists):
        sessions.create(feed_id=feed, created_by="dev-operator")


def test_get_and_get_by_feed(sessions, session, feed):
    assert sessions.get(session.id) == session
    assert sessions.get_by_feed(feed) == session
    assert sessions.get_by_feed(uuid.uuid4()) is None
    with pytest.raises(OnboardingNotFound):
        sessions.get(uuid.uuid4())


def test_lock_reads_the_row_fresh(s, sessions, session):
    s.execute(text("UPDATE onboarding_sessions SET model_calls = 7 WHERE id = :id"), {"id": session.id})
    assert sessions.lock(session.id).model_calls == 7
    with pytest.raises(OnboardingNotFound):
        sessions.lock(uuid.uuid4())


def test_list_filters_by_status_and_lists_oldest_first(s, sessions, session):
    other_feed = FeedRepository(s).create_candidate(code="syn_onboarding_repo_2", name="Repo 2").id
    other = sessions.create(feed_id=other_feed, created_by="dev-operator")
    sessions.finish(other.id)
    assert [x.id for x in sessions.list()] == [session.id, other.id]
    assert [x.id for x in sessions.list("active")] == [session.id]
    assert [x.id for x in sessions.list("done")] == [other.id]


def test_set_step_state_changes_one_step_only(sessions, session):
    changed = sessions.set_step_state(session.id, "profile", "agent_working")
    assert changed.step_states["profile"] == "agent_working"
    assert {k: v for k, v in changed.step_states.items() if k != "profile"} == \
        {k: v for k, v in session.step_states.items() if k != "profile"}
    assert changed.updated_at >= session.updated_at
    assert sessions.get(session.id).step_states["profile"] == "agent_working"


@pytest.mark.parametrize(("step", "state"), [("shipping", "done"), ("profile", "paused")])
def test_an_unknown_step_or_state_is_refused_before_the_database(sessions, session, step, state):
    with pytest.raises(ValueError):
        sessions.set_step_state(session.id, step, state)


def test_move_to_changes_the_current_step(sessions, session):
    assert sessions.move_to(session.id, "glossary").current_step == "glossary"
    with pytest.raises(ValueError):
        sessions.move_to(session.id, "shipping")


def test_finish_marks_the_session_done(sessions, session):
    assert sessions.finish(session.id).status == "done"


def test_events_come_back_in_the_order_they_were_added(s, sessions, session, make_proposal):
    proposal = make_proposal(kind="profile_summary")
    sessions.add_event(session.id, step="profile", event="entered", actor="dev-operator")
    sessions.add_event(session.id, step="profile", event="agent_queued", actor="system")
    sessions.add_event(session.id, step="profile", event="pre_accepted", actor="system", proposal_id=proposal.id,
                       detail={"confidence": 0.93})
    events = sessions.events(session.id)
    assert [e.event for e in events] == ["entered", "agent_queued", "pre_accepted"]
    assert (events[2].proposal_id, events[2].detail, events[2].actor, events[2].step) == \
        (proposal.id, {"confidence": 0.93}, "system", "profile")
    assert events[0].detail == {} and events[0].proposal_id is None and events[0].at <= events[2].at
    assert sessions.events(uuid.uuid4()) == []


def test_an_unknown_event_is_refused_before_the_database(sessions, session):
    with pytest.raises(ValueError):
        sessions.add_event(session.id, step="profile", event="vanished", actor="system")
    with pytest.raises(ValueError):
        sessions.add_event(session.id, step="shipping", event="entered", actor="system")


def test_bump_model_calls_counts_and_returns_the_total(sessions, session):
    assert sessions.bump_model_calls(session.id) == 1
    assert sessions.bump_model_calls(session.id, by=3) == 4
    assert sessions.get(session.id).model_calls == 4
    with pytest.raises(ValueError):
        sessions.bump_model_calls(session.id, by=-1)


# ---------------------------------------------------------------- jobs

def test_queue_makes_a_queued_job(jobs, session):
    job, created = jobs.queue(session_id=session.id, step="profile")
    assert created is True
    assert (job.session_id, job.step, job.status, job.attempt, job.lease_until, job.error, job.started_at,
            job.finished_at) == (session.id, "profile", "queued", 0, None, None, None, None)
    assert jobs.get(job.id) == job
    assert jobs.active_for_session(session.id) == job


def test_queue_again_returns_the_active_job(jobs, session):
    first, _ = jobs.queue(session_id=session.id, step="profile")
    again, created = jobs.queue(session_id=session.id, step="glossary")
    assert created is False and again.id == first.id and again.step == "profile"


def test_no_active_job(jobs, session):
    assert jobs.active_for_session(session.id) is None


def test_claim_takes_the_oldest_queued_job_with_a_lease(s, sessions, jobs, session):
    other_feed = FeedRepository(s).create_candidate(code="syn_onboarding_repo_3", name="Repo 3").id
    other = sessions.create(feed_id=other_feed, created_by="dev-operator")
    first, _ = jobs.queue(session_id=session.id, step="profile")
    second, _ = jobs.queue(session_id=other.id, step="profile")
    claimed = jobs.claim(lease_seconds=60)
    assert (claimed.id, claimed.status, claimed.attempt) == (first.id, "running", 1)
    assert claimed.lease_until is not None and claimed.started_at is not None
    assert jobs.claim(lease_seconds=60).id == second.id
    assert jobs.claim(lease_seconds=60) is None


def test_claim_with_nothing_queued(jobs):
    assert jobs.claim(lease_seconds=60) is None


def test_a_job_whose_lease_passed_is_taken_again_on_the_next_attempt(jobs, session):
    job, _ = jobs.queue(session_id=session.id, step="profile")
    first = jobs.claim(lease_seconds=0)
    assert first.attempt == 1
    again = jobs.claim(lease_seconds=60)
    assert (again.id, again.status, again.attempt) == (job.id, "running", 2)
    assert jobs.claim(lease_seconds=60) is None  # the new lease holds


def test_a_job_that_ran_out_of_attempts_fails_instead_of_running_again(jobs, session):
    job, _ = jobs.queue(session_id=session.id, step="profile")
    for attempt in range(1, MAX_JOB_ATTEMPTS + 1):
        assert jobs.claim(lease_seconds=0).attempt == attempt
    assert jobs.claim(lease_seconds=60) is None
    failed = jobs.get(job.id)
    assert failed.status == "failed" and "gave up" in failed.error and failed.finished_at is not None
    assert jobs.active_for_session(session.id) is None


def test_finish_marks_a_running_job_done(jobs, session):
    jobs.queue(session_id=session.id, step="profile")
    claimed = jobs.claim(lease_seconds=60)
    done = jobs.finish(claimed.id, attempt=claimed.attempt)
    assert done.status == "done" and done.finished_at is not None and done.error is None
    assert jobs.active_for_session(session.id) is None


def test_fail_keeps_the_error(jobs, session):
    jobs.queue(session_id=session.id, step="profile")
    claimed = jobs.claim(lease_seconds=60)
    failed = jobs.fail(claimed.id, attempt=claimed.attempt, error="The model did not answer.")
    assert (failed.status, failed.error) == ("failed", "The model did not answer.")


def test_an_answer_after_the_lease_was_taken_again_is_refused(jobs, session):
    jobs.queue(session_id=session.id, step="profile")
    first = jobs.claim(lease_seconds=0)
    second = jobs.claim(lease_seconds=60)
    with pytest.raises(JobSuperseded):
        jobs.finish(first.id, attempt=first.attempt)
    with pytest.raises(JobSuperseded):
        jobs.fail(first.id, attempt=first.attempt, error="late")
    assert jobs.finish(second.id, attempt=second.attempt).status == "done"


def test_finishing_a_job_that_is_not_running_is_refused(jobs, session):
    job, _ = jobs.queue(session_id=session.id, step="profile")
    with pytest.raises(JobSuperseded):
        jobs.finish(job.id, attempt=0)


def test_discard_ends_a_queued_or_running_job(jobs, session):
    job, _ = jobs.queue(session_id=session.id, step="profile")
    assert jobs.discard(job.id).status == "discarded"
    again, created = jobs.queue(session_id=session.id, step="glossary")  # the session may queue another now
    assert created is True and again.id != job.id
    jobs.claim(lease_seconds=60)
    assert jobs.discard(again.id).status == "discarded"


def test_discarding_a_finished_job_is_a_no_op(jobs, session):
    jobs.queue(session_id=session.id, step="profile")
    claimed = jobs.claim(lease_seconds=60)
    jobs.finish(claimed.id, attempt=1)
    assert jobs.discard(claimed.id).status == "done"


def test_the_jobs_of_a_session_are_listed_oldest_first(jobs, session):
    first, _ = jobs.queue(session_id=session.id, step="profile")
    jobs.discard(first.id)
    second, _ = jobs.queue(session_id=session.id, step="glossary")
    assert [j.id for j in jobs.list_for_session(session.id)] == [first.id, second.id]
