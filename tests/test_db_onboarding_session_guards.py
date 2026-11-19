"""Migration 0024's onboarding tables (phase 7.8 plan, assumptions 31 to 33, 38), by raw SQL: the repositories are
bypassed on purpose.

Each refusal goes through answered_by(): the statement is refused with the guard's message, and with only that
guard removed (inside a savepoint rolled back at once) it is no longer refused with that message. Every check on a
nullable column has its NULL case.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from core.repositories.feeds import FeedRepository
from tests.test_db_onboarding_guards import _branch_off, answered_by, refused

STEPS = ("upload", "profile", "glossary", "mapping", "transformations", "rules", "go_live", "first_load",
         "product_ready")
STATES = {step: "waiting" for step in STEPS}

SESSION = ("INSERT INTO onboarding_sessions (feed_id, current_step, step_states, status, created_by) "
           "VALUES (:feed, :step, CAST(:states AS jsonb), :status, :by) RETURNING id")
EVENT = ("INSERT INTO onboarding_events (session_id, step, event, actor, proposal_id, detail) "
         "VALUES (:sid, :step, :event, :actor, :proposal, CAST(:detail AS jsonb)) RETURNING id")
JOB = "INSERT INTO onboarding_jobs (session_id, step) VALUES (:sid, :step) RETURNING id"  # not :session: refused() has one


@pytest.fixture
def s(db_session):
    return db_session


@pytest.fixture
def feed(s):
    return FeedRepository(s).create_candidate(code="syn_onboarding_guards", name="Guards").id


def session_params(feed, **over):
    return {"feed": feed, "step": "profile", "states": json.dumps(STATES), "status": "active",
            "by": "dev-data-owner", **over}


@pytest.fixture
def session(s, feed):
    return s.execute(text(SESSION), session_params(feed)).scalar_one()


def drop_check(table, name):
    return f"ALTER TABLE {table} DROP CONSTRAINT {name}"


# ---------------------------------------------------------------- sessions: the legal path and the CHECKs

def test_a_session_as_described_is_accepted(s, feed):
    s.execute(text(SESSION), session_params(feed))
    row = s.execute(text("SELECT model_calls, created_at IS NOT NULL AS has_created, updated_at IS NOT NULL AS has_updated "
                         "FROM onboarding_sessions")).one()
    assert (row.model_calls, row.has_created, row.has_updated) == (0, True, True)


@pytest.mark.parametrize("step", STEPS)
def test_every_step_is_a_legal_current_step(s, feed, step):
    s.execute(text(SESSION), session_params(feed, step=step))


def test_an_unknown_current_step_is_refused(s, feed):
    answered_by(s, "ck_onboarding_sessions_current_step", SESSION, session_params(feed, step="shipping"),
                drop_check("onboarding_sessions", "ck_onboarding_sessions_current_step"))


def test_an_unknown_status_is_refused(s, feed):
    answered_by(s, "ck_onboarding_sessions_status", SESSION, session_params(feed, status="paused"),
                drop_check("onboarding_sessions", "ck_onboarding_sessions_status"))


def test_a_blank_creator_is_refused(s, feed):
    answered_by(s, "ck_onboarding_sessions_created_by", SESSION, session_params(feed, by="  "),
                drop_check("onboarding_sessions", "ck_onboarding_sessions_created_by"))


def _without(key):
    return {k: v for k, v in STATES.items() if k != key}


@pytest.mark.parametrize("states", [
    _without("go_live"),                                    # a step missing
    {**STATES, "shipping": "waiting"},                      # an extra key
    {**STATES, "mapping": "paused"},                        # a value outside the seven
    {**STATES, "mapping": 5},                               # a value that is not a string
    {**STATES, "mapping": None},                            # a JSON null value
    {}, [], "waiting", None,                                # not an object (None is the JSON null)
])
def test_step_states_hold_exactly_the_nine_steps_with_known_states(s, feed, states):
    answered_by(s, "ck_onboarding_sessions_step_states", SESSION,
                session_params(feed, states=json.dumps(states)),
                drop_check("onboarding_sessions", "ck_onboarding_sessions_step_states"))


@pytest.mark.parametrize("state", ["waiting", "agent_working", "needs_decision", "ready", "done", "stale", "failed"])
def test_every_state_is_legal(s, feed, state):
    s.execute(text(SESSION), session_params(feed, states=json.dumps({**STATES, "mapping": state})))


def test_a_sql_null_for_step_states_is_refused(s, feed):
    refused(s, "null value in column .step_states.", SESSION.replace("CAST(:states AS jsonb)", "NULL"),
            **{k: v for k, v in session_params(feed).items() if k != "states"})


def test_model_calls_cannot_be_negative(s, feed):
    answered_by(s, "ck_onboarding_sessions_model_calls", SESSION.replace("created_by)", "created_by, model_calls)")
                .replace(":by)", ":by, -1)"), session_params(feed),
                drop_check("onboarding_sessions", "ck_onboarding_sessions_model_calls"))


def test_a_feed_has_one_session(s, feed, session):
    answered_by(s, "uq_onboarding_sessions_feed_id", SESSION, session_params(feed),
                drop_check("onboarding_sessions", "uq_onboarding_sessions_feed_id"))


# ---------------------------------------------------------------- sessions: workflow state, not configuration

def test_the_workflow_columns_can_change(s, session):
    s.execute(text("UPDATE onboarding_sessions SET current_step = 'glossary', status = 'active', model_calls = 3, "
                   "step_states = CAST(:states AS jsonb), updated_at = clock_timestamp()"),
              {"states": json.dumps({**STATES, "upload": "done", "profile": "done", "glossary": "agent_working"})})
    row = s.execute(text("SELECT current_step, model_calls, step_states ->> 'glossary' AS g FROM onboarding_sessions")).one()
    assert (row.current_step, row.model_calls, row.g) == ("glossary", 3, "agent_working")
    s.execute(text("UPDATE onboarding_sessions SET status = 'done'"))


@pytest.mark.parametrize("change", [
    "feed_id = (SELECT id FROM feeds WHERE code <> 'syn_onboarding_guards' LIMIT 1)",
    "created_by = 'someone-else'",
    "created_at = created_at - interval '1 day'",
    "id = gen_random_uuid()",
])
def test_the_feed_creator_and_creation_time_are_fixed(s, feed, session, change):
    FeedRepository(s).create_candidate(code="syn_onboarding_other", name="Other")
    answered_by(s, "are fixed", f"UPDATE onboarding_sessions SET {change}", {},
                _branch_off("amaze_guard_onboarding_session",
                            "onboarding_sessions: the feed, creator and creation time of session % are fixed"))


def test_model_calls_never_goes_down(s, session):
    s.execute(text("UPDATE onboarding_sessions SET model_calls = 5"))
    answered_by(s, "only grows", "UPDATE onboarding_sessions SET model_calls = 4", {},
                _branch_off("amaze_guard_onboarding_session", "onboarding_sessions: model_calls of session % only grows"))


def test_a_session_is_never_deleted(s, session):
    answered_by(s, "DELETE refused", "DELETE FROM onboarding_sessions", {},
                _branch_off("amaze_guard_onboarding_session", "onboarding_sessions: DELETE refused; a session is history"))


def test_truncate_of_sessions_is_refused(s, session):
    # CASCADE reaches the events and jobs tables too, whose own guards refuse as well: all three are removed for the proof
    answered_by(s, "TRUNCATE refused", "TRUNCATE onboarding_sessions CASCADE", {},
                "DO $$ BEGIN DROP TRIGGER trg_onboarding_sessions_no_truncate ON onboarding_sessions; "
                "DROP TRIGGER trg_onboarding_events_no_truncate ON onboarding_events; "
                "DROP TRIGGER trg_onboarding_jobs_no_truncate ON onboarding_jobs; END $$")


# ---------------------------------------------------------------- events: append-only

def event_params(session, **over):
    return {"sid": session, "step": "profile", "event": "entered", "actor": "dev-data-owner", "proposal": None,
            "detail": "{}", **over}


@pytest.mark.parametrize("event", ["entered", "agent_queued", "agent_started", "agent_done", "agent_failed",
                                   "pre_accepted", "edited", "approved", "rejected", "back", "stale", "done",
                                   "call_cap_reached"])
def test_every_event_is_legal(s, session, event):
    s.execute(text(EVENT), event_params(session, event=event))


def test_an_event_defaults_its_time_and_detail(s, session):
    s.execute(text("INSERT INTO onboarding_events (session_id, step, event, actor) VALUES (:s, 'profile', 'entered', 'system')"),
              {"s": session})
    row = s.execute(text("SELECT at IS NOT NULL AS has_at, detail FROM onboarding_events")).one()
    assert (row.has_at, row.detail) == (True, {})


def test_an_unknown_event_is_refused(s, session):
    answered_by(s, "ck_onboarding_events_event", EVENT, event_params(session, event="vanished"),
                drop_check("onboarding_events", "ck_onboarding_events_event"))


def test_an_unknown_event_step_is_refused(s, session):
    answered_by(s, "ck_onboarding_events_step", EVENT, event_params(session, step="shipping"),
                drop_check("onboarding_events", "ck_onboarding_events_step"))


def test_a_blank_actor_is_refused(s, session):
    answered_by(s, "ck_onboarding_events_actor", EVENT, event_params(session, actor=" "),
                drop_check("onboarding_events", "ck_onboarding_events_actor"))


@pytest.mark.parametrize("detail", ["[]", '"x"', "5", "null"])
def test_the_detail_is_a_json_object(s, session, detail):
    answered_by(s, "ck_onboarding_events_detail_is_an_object", EVENT, event_params(session, detail=detail),
                drop_check("onboarding_events", "ck_onboarding_events_detail_is_an_object"))


def test_an_event_belongs_to_a_session(s):
    refused(s, "fk_onboarding_events_session_id_onboarding_sessions", EVENT,
            **event_params("00000000-0000-0000-0000-000000000001"))


def test_an_event_may_name_a_proposal_and_the_proposal_must_exist(s, session, make_proposal):
    proposal = make_proposal(kind="profile_summary")
    s.execute(text(EVENT), event_params(session, proposal=proposal.id))
    refused(s, "fk_onboarding_events_proposal_id_proposals", EVENT,
            **event_params(session, proposal="00000000-0000-0000-0000-000000000001"))


@pytest.mark.parametrize("statement", ["UPDATE onboarding_events SET actor = 'x'", "DELETE FROM onboarding_events",
                                       "TRUNCATE onboarding_events"])
def test_events_are_append_only(s, session, statement):
    s.execute(text(EVENT), event_params(session))
    refused(s, "onboarding_events is append-only|onboarding_events: TRUNCATE|append-only", statement)


# ---------------------------------------------------------------- jobs

def job_params(session, step="profile"):
    return {"sid": session, "step": step}


def job_in(s, session, state, *, step="profile", expired=False):
    """A job reached by legal moves: queued, running (lease passed if expired), then finished."""
    job = s.execute(text(JOB), job_params(session, step)).scalar_one()
    if state == "queued":
        return job
    lease = "clock_timestamp() - interval '5 seconds'" if expired else "clock_timestamp() + interval '60 seconds'"
    s.execute(text(f"UPDATE onboarding_jobs SET status = 'running', attempt = 1, started_at = clock_timestamp(), "
                   f"lease_until = {lease} WHERE id = :id"), {"id": job})
    if state == "running":
        return job
    extra = ", error = 'boom'" if state == "failed" else ""
    s.execute(text(f"UPDATE onboarding_jobs SET status = '{state}', finished_at = clock_timestamp(){extra} WHERE id = :id"),
              {"id": job})
    return job


def test_a_job_starts_queued_at_attempt_zero(s, session):
    s.execute(text(JOB), job_params(session))
    row = s.execute(text("SELECT status, attempt, lease_until, error, started_at, finished_at, created_at IS NOT NULL AS c "
                         "FROM onboarding_jobs")).one()
    assert (row.status, row.attempt, row.lease_until, row.error, row.started_at, row.finished_at, row.c) == \
        ("queued", 0, None, None, None, None, True)


@pytest.mark.parametrize("params", [{"status": "running", "attempt": 0}, {"status": "done", "attempt": 0},
                                    {"status": "queued", "attempt": 1}])
def test_a_new_job_is_queued_at_attempt_zero_only(s, session, params):
    sql = ("INSERT INTO onboarding_jobs (session_id, step, status, attempt, lease_until, finished_at) VALUES "
           "(:sid, 'profile', :status, :attempt, clock_timestamp() + interval '1 minute', "
           "CASE WHEN :status = 'done' THEN clock_timestamp() END)")
    answered_by(s, "a new job is queued at attempt 0", sql, {"sid": session, **params},
                _branch_off("amaze_guard_onboarding_job", "onboarding_jobs: a new job is queued at attempt 0, not % at %"))


def test_an_unknown_job_step_is_refused(s, session):
    answered_by(s, "ck_onboarding_jobs_step", JOB, job_params(session, "shipping"),
                drop_check("onboarding_jobs", "ck_onboarding_jobs_step"))


def test_a_session_has_one_active_job(s, session):
    first = job_in(s, session, "queued")
    answered_by(s, "uq_onboarding_jobs_one_active_per_session", JOB, job_params(session, "glossary"),
                "DROP INDEX uq_onboarding_jobs_one_active_per_session")
    s.execute(text("UPDATE onboarding_jobs SET status = 'discarded', finished_at = clock_timestamp() WHERE id = :id"),
              {"id": first})
    s.execute(text(JOB), job_params(session, "glossary"))  # the first is finished: the session may queue another


def test_a_running_job_is_active_too(s, session):
    job_in(s, session, "running")
    answered_by(s, "uq_onboarding_jobs_one_active_per_session", JOB, job_params(session),
                "DROP INDEX uq_onboarding_jobs_one_active_per_session")


def test_finished_jobs_pile_up_without_limit(s, session):
    for state in ("done", "failed", "discarded", "done"):
        job_in(s, session, state)


MOVES = [
    # (from, to-sql, why it is legal)
    ("queued", "status = 'running', attempt = 1, started_at = clock_timestamp(), lease_until = clock_timestamp() + interval '1 minute'"),
    ("queued", "status = 'discarded', finished_at = clock_timestamp()"),
    ("running", "status = 'done', finished_at = clock_timestamp()"),
    ("running", "status = 'failed', error = 'boom', finished_at = clock_timestamp()"),
    ("running", "status = 'discarded', finished_at = clock_timestamp()"),
]


@pytest.mark.parametrize(("start", "move"), MOVES)
def test_the_legal_moves(s, session, start, move):
    job = job_in(s, session, start)
    s.execute(text(f"UPDATE onboarding_jobs SET {move} WHERE id = :id"), {"id": job})


def test_a_running_job_whose_lease_passed_goes_back_to_queued(s, session):
    job = job_in(s, session, "running", expired=True)
    s.execute(text("UPDATE onboarding_jobs SET status = 'queued' WHERE id = :id"), {"id": job})
    s.execute(text("UPDATE onboarding_jobs SET status = 'running', attempt = 2, lease_until = clock_timestamp() + "
                   "interval '1 minute' WHERE id = :id"), {"id": job})


CANNOT_MOVE = _branch_off("amaze_guard_onboarding_job",
                          "onboarding_jobs: job % cannot move from % (attempt %) to % (attempt %)")

ILLEGAL = [
    ("queued", "status = 'done', finished_at = clock_timestamp()"),
    ("queued", "status = 'failed', error = 'boom', finished_at = clock_timestamp()"),
    ("queued", "lease_until = clock_timestamp()"),                                       # queued to queued
    ("queued", "status = 'running', attempt = 0, lease_until = clock_timestamp() + interval '1 minute'"),
    ("queued", "status = 'running', attempt = 2, lease_until = clock_timestamp() + interval '1 minute'"),
    ("running", "status = 'running', attempt = 2"),                                       # taken again without the queue
    ("running", "status = 'done', attempt = 2, finished_at = clock_timestamp()"),
    ("running", "status = 'queued'"),                                                     # the lease has not passed
]


@pytest.mark.parametrize(("start", "move"), ILLEGAL)
def test_the_illegal_moves(s, session, start, move):
    job = job_in(s, session, start)
    answered_by(s, "cannot move from", f"UPDATE onboarding_jobs SET {move} WHERE id = :id", {"id": job}, CANNOT_MOVE)


@pytest.mark.parametrize("finished", ["done", "failed", "discarded"])
@pytest.mark.parametrize("move", ["status = 'queued'", "status = 'running', attempt = 5", "error = 'late'"])
def test_a_finished_job_never_changes(s, session, finished, move):
    job = job_in(s, session, finished)
    answered_by(s, "never changes", f"UPDATE onboarding_jobs SET {move} WHERE id = :id", {"id": job},
                _branch_off("amaze_guard_onboarding_job", "onboarding_jobs: job % is %, and a finished job never changes"))


@pytest.mark.parametrize("change", ["step = 'rules'", "created_at = created_at - interval '1 day'"])
def test_the_session_step_and_creation_of_a_job_are_fixed(s, session, change):
    job = job_in(s, session, "queued")
    answered_by(s, "are fixed", f"UPDATE onboarding_jobs SET {change} WHERE id = :id", {"id": job},
                _branch_off("amaze_guard_onboarding_job", "onboarding_jobs: the session and step of job % are fixed"))


def test_a_job_is_never_deleted(s, session):
    job_in(s, session, "queued")
    answered_by(s, "DELETE refused", "DELETE FROM onboarding_jobs", {},
                _branch_off("amaze_guard_onboarding_job", "onboarding_jobs: DELETE refused; a job is history"))


def test_truncate_of_jobs_is_refused(s, session):
    job_in(s, session, "queued")
    answered_by(s, "TRUNCATE refused", "TRUNCATE onboarding_jobs CASCADE", {},
                "DROP TRIGGER trg_onboarding_jobs_no_truncate ON onboarding_jobs")


# ---------------------------------------------------------------- jobs: the CHECKs on nullable columns (their NULL cases)

def test_a_running_job_needs_a_lease(s, session):
    job = job_in(s, session, "queued")
    answered_by(s, "ck_onboarding_jobs_running_has_a_lease",
                "UPDATE onboarding_jobs SET status = 'running', attempt = 1, lease_until = NULL WHERE id = :id",
                {"id": job}, drop_check("onboarding_jobs", "ck_onboarding_jobs_running_has_a_lease"))


def test_a_failed_job_has_an_error_and_only_a_failed_one(s, session):
    job = job_in(s, session, "running")
    answered_by(s, "ck_onboarding_jobs_failed_has_an_error",
                "UPDATE onboarding_jobs SET status = 'failed', finished_at = clock_timestamp() WHERE id = :id",
                {"id": job}, drop_check("onboarding_jobs", "ck_onboarding_jobs_failed_has_an_error"))
    answered_by(s, "ck_onboarding_jobs_failed_has_an_error",
                "UPDATE onboarding_jobs SET status = 'done', error = 'x', finished_at = clock_timestamp() WHERE id = :id",
                {"id": job}, drop_check("onboarding_jobs", "ck_onboarding_jobs_failed_has_an_error"))


@pytest.mark.parametrize("status", ["done", "failed", "discarded"])
def test_a_finished_job_has_a_finish_time(s, session, status):
    job = job_in(s, session, "running")
    error = ", error = 'x'" if status == "failed" else ""
    answered_by(s, "ck_onboarding_jobs_finished_at_matches_status",
                f"UPDATE onboarding_jobs SET status = '{status}'{error} WHERE id = :id", {"id": job},
                drop_check("onboarding_jobs", "ck_onboarding_jobs_finished_at_matches_status"))


def test_a_job_that_is_not_finished_has_no_finish_time(s, session):
    answered_by(s, "ck_onboarding_jobs_finished_at_matches_status",
                "INSERT INTO onboarding_jobs (session_id, step, finished_at) VALUES (:sid, 'profile', clock_timestamp())",
                {"sid": session}, drop_check("onboarding_jobs", "ck_onboarding_jobs_finished_at_matches_status"))


def test_the_attempt_cannot_be_negative(s, session):
    """The job guard refuses first (a new job is at attempt 0); the CHECK is the second line."""
    refused(s, "a new job is queued at attempt 0", "INSERT INTO onboarding_jobs (session_id, step, attempt) "
            "VALUES (:sid, 'profile', -1)", sid=session)
