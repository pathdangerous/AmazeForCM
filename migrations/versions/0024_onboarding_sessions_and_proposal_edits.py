"""onboarding sessions, events and jobs; editing a proposal before approval (superseded)

Revision ID: 0024
Revises: 0023

Phase 7.8, stage S5 (task 5.2; plan assumptions 31 to 33, 36, 38 and 39).

Onboarding tables
- onboarding_sessions: one per feed. Workflow state, not configuration: it may change, but only current_step,
  step_states, status, updated_at and model_calls (trg_onboarding_sessions_a_guard); the feed, the creator and the
  creation time are fixed; DELETE and TRUNCATE are refused; model_calls only grows. amaze_valid_step_states() is the
  CHECK on step_states: an object whose keys are exactly the nine steps, each with one of seven states.
- onboarding_events: append-only (the 0002 function). The audit timeline reads it, with proposals, approvals and
  proposal_log.
- onboarding_jobs: the queue of the background worker, copied from glossary_runs (0016): a lease, an attempt count,
  one active (queued or running) job per session, status moves queued -> running -> done | failed | discarded, a queued
  job may be discarded, and a running job whose lease has passed goes back to queued (trg_onboarding_jobs_a_guard). A
  finished job never changes; DELETE and TRUNCATE are refused.

Editing a proposal before it is approved (the phase 5 "no edit" rule changes; every other guard stays)
- An edit is a NEW proposals row: the same proposal_log_id (the log keeps what the model said), kind, feed, requirement,
  autonomy and approver roles, `edited_from` the old row, `edited_by` the person, and the edited payload. The old row
  moves to the new status `superseded` and can never be decided: no approvals row (trg_approvals_not_superseded), no
  status change (0016's "a decided proposal never changes again" now covers it), no second edit.
- Order. The "one pending proposal per column" indexes (0016, 0018, 0023) would refuse the new pending row while the
  old one is still pending. So the BEFORE INSERT trigger of the new row (trg_proposals_edit) checks everything, then
  moves the old row to superseded itself, before the index sees the new row (BEFORE triggers run first). The edit is
  one statement and cannot be half done. amaze_edit_proposal(old_id, new_payload, edited_by) is the one place that
  inserts the row; the repository calls it. A raw INSERT with edited_from is checked by the same trigger.
- A raw UPDATE of a pending row to superseded is a status change the proposal guard allows, so a CONSTRAINT trigger,
  DEFERRABLE INITIALLY DEFERRED (trg_proposals_superseded_has_an_edit), refuses at commit any superseded row that has no
  edit. This is chosen over a session variable that the edit would set: a variable can be set by any connection
  silently (the reason plan assumption 9 chose a table for the four-eyes setting), and the commit-time check needs no
  secret handshake.
- Only four kinds can be edited: glossary_element, mapping, transformation, rule. What an edit may change:
  the column (source_field) and rule key never; a mapping's candidates never, and its recommended element must be one of
  them (so an edit "picks another candidate"); a glossary proposal's options keep their number, kind, key and alias,
  and may change the element's name, definition, type and aliases (the 0016 element guard then compares the row with the
  EDITED option, never the old one); a rule's SQL and description, a transformation's steps may change. Every
  configuration guard (0005, 0016, 0018, 0023) reads the proposal row, so it applies to the edited row as to any.
- An edit starts pending, with all its approval steps still to come; approvals given to the old row stay on the old row.

Downgrade stops, naming 0024, while any superseded proposal or any onboarding session exists, then restores the 0016
proposal guard and the 0002 status list. Function bodies are written out here on purpose: a migration must not change
when code changes. Every check on a nullable value spells NULL out. PostgreSQL 14 features only: plpgsql, jsonb,
constraint triggers with a WHEN clause, partial unique indexes.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

STEPS = ("upload", "profile", "glossary", "mapping", "transformations", "rules", "go_live", "first_load",
         "product_ready")
STATES = ("waiting", "agent_working", "needs_decision", "ready", "done", "stale", "failed")
SESSION_STATUSES = ("active", "done")
EVENTS = ("entered", "agent_queued", "agent_started", "agent_done", "agent_failed", "pre_accepted", "edited",
          "approved", "rejected", "back", "stale", "done", "call_cap_reached")
JOB_STATUSES = ("queued", "running", "done", "failed", "discarded")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(repr(v) for v in values) + "]"


VALID_STEP_STATES = f"""
CREATE FUNCTION amaze_valid_step_states(states jsonb) RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    k text;
    v jsonb;
BEGIN
    IF states IS NULL OR jsonb_typeof(states) IS DISTINCT FROM 'object' THEN
        RETURN false;
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(states)) <> {len(STEPS)} THEN
        RETURN false;
    END IF;
    FOREACH k IN ARRAY {_array(STEPS)} LOOP
        v := states -> k;  -- a missing step reads as NULL
        IF v IS NULL OR jsonb_typeof(v) IS DISTINCT FROM 'string' THEN
            RETURN false;
        END IF;
        IF NOT ((v #>> '{{}}') = ANY({_array(STATES)})) THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END $$;
"""

SESSION_GUARD = """
CREATE FUNCTION amaze_guard_onboarding_session() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    movable text[] := ARRAY['current_step', 'step_states', 'status', 'updated_at', 'model_calls'];
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'onboarding_sessions: DELETE refused; a session is history' USING ERRCODE = 'restrict_violation';
    END IF;
    IF (to_jsonb(NEW) - movable) IS DISTINCT FROM (to_jsonb(OLD) - movable) THEN
        RAISE EXCEPTION 'onboarding_sessions: the feed, creator and creation time of session % are fixed', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.model_calls < OLD.model_calls THEN
        RAISE EXCEPTION 'onboarding_sessions: model_calls of session % only grows', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

JOB_GUARD = """
CREATE FUNCTION amaze_guard_onboarding_job() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    movable text[] := ARRAY['status', 'attempt', 'lease_until', 'started_at', 'finished_at', 'error'];
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'onboarding_jobs: DELETE refused; a job is history' USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'queued' OR NEW.attempt IS DISTINCT FROM 0 THEN
            RAISE EXCEPTION 'onboarding_jobs: a new job is queued at attempt 0, not % at %', NEW.status, NEW.attempt
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - movable) IS DISTINCT FROM (to_jsonb(OLD) - movable) THEN
        RAISE EXCEPTION 'onboarding_jobs: the session and step of job % are fixed', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.status = 'done' OR OLD.status = 'failed' OR OLD.status = 'discarded' THEN
        RAISE EXCEPTION 'onboarding_jobs: job % is %, and a finished job never changes', OLD.id, OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (OLD.status = 'queued' AND NEW.status = 'running' AND NEW.attempt = OLD.attempt + 1)
       OR (OLD.status = 'queued' AND NEW.status = 'discarded' AND NEW.attempt = OLD.attempt)
       OR (OLD.status = 'running' AND (NEW.status = 'done' OR NEW.status = 'failed' OR NEW.status = 'discarded')
           AND NEW.attempt = OLD.attempt)
       OR (OLD.status = 'running' AND NEW.status = 'queued' AND NEW.attempt = OLD.attempt
           AND OLD.lease_until < clock_timestamp()) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'onboarding_jobs: job % cannot move from % (attempt %) to % (attempt %)', OLD.id, OLD.status,
        OLD.attempt, NEW.status, NEW.attempt USING ERRCODE = 'check_violation';
END $$;
"""

# The 0016 body, which downgrade restores (it allows approved and rejected only).
PROPOSAL_CHANGE_0016 = """
CREATE OR REPLACE FUNCTION amaze_guard_proposal_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'proposals: DELETE refused; a proposal is decided, never removed'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (to_jsonb(NEW) - 'status' - 'decided_at') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'decided_at') THEN
        RAISE EXCEPTION 'proposals: only status and decided_at change (by a decision); proposal % is otherwise fixed',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.status IS DISTINCT FROM 'pending' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
        RAISE EXCEPTION 'proposals: proposal % is %, and a decided proposal never changes again', OLD.id, OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
       AND NEW.status IS DISTINCT FROM 'approved' AND NEW.status IS DISTINCT FROM 'rejected' THEN
        RAISE EXCEPTION 'proposals: a pending proposal moves only to approved or rejected, not %', NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;
"""

# 0024: a pending proposal may also move to superseded (the message of the status rule is kept: its tests name it).
PROPOSAL_CHANGE_0024 = PROPOSAL_CHANGE_0016.replace(
    "AND NEW.status IS DISTINCT FROM 'approved' AND NEW.status IS DISTINCT FROM 'rejected' THEN",
    "AND NEW.status IS DISTINCT FROM 'approved' AND NEW.status IS DISTINCT FROM 'rejected'\n"
    "       AND NEW.status IS DISTINCT FROM 'superseded' THEN")

PROPOSAL_EDIT_GUARD = """
CREATE FUNCTION amaze_guard_proposal_edit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    o proposals%ROWTYPE;
    old_options jsonb;
    new_options jsonb;
    n integer;
BEGIN
    SELECT * INTO o FROM proposals WHERE id = NEW.edited_from FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'proposals: proposal % to edit does not exist', NEW.edited_from
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF o.status IS DISTINCT FROM 'pending' THEN
        RAISE EXCEPTION 'proposals: proposal % is %, and only a pending proposal can be edited', o.id, o.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM 'pending' THEN
        RAISE EXCEPTION 'proposals: an edited proposal starts pending, not %', NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF o.kind NOT IN ('glossary_element', 'mapping', 'transformation', 'rule') THEN
        RAISE EXCEPTION 'proposals: a % proposal cannot be edited', o.kind USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.proposal_log_id IS DISTINCT FROM o.proposal_log_id THEN
        RAISE EXCEPTION 'proposals: an edit keeps the logged call of proposal %', o.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind IS DISTINCT FROM o.kind THEN
        RAISE EXCEPTION 'proposals: an edit keeps the kind of proposal %', o.id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.feed_id IS DISTINCT FROM o.feed_id THEN
        RAISE EXCEPTION 'proposals: an edit keeps the feed of proposal %', o.id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.requirement_id IS DISTINCT FROM o.requirement_id THEN
        RAISE EXCEPTION 'proposals: an edit keeps the requirement of proposal %', o.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.autonomy IS DISTINCT FROM o.autonomy OR NEW.required_approver_roles IS DISTINCT FROM o.required_approver_roles THEN
        RAISE EXCEPTION 'proposals: an edit keeps the autonomy and approver roles of proposal %', o.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF jsonb_typeof(NEW.payload) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'proposals: the edit of proposal % must be a JSON object', o.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.payload IS NOT DISTINCT FROM o.payload THEN
        RAISE EXCEPTION 'proposals: the edit of proposal % changes nothing', o.id USING ERRCODE = 'check_violation';
    END IF;
    IF o.kind IN ('glossary_element', 'mapping', 'transformation')
       AND (NEW.payload ->> 'source_field') IS DISTINCT FROM (o.payload ->> 'source_field') THEN
        RAISE EXCEPTION 'proposals: an edit of proposal % keeps its column', o.id USING ERRCODE = 'check_violation';
    END IF;
    IF o.kind = 'rule' AND (NEW.payload ->> 'rule_key') IS DISTINCT FROM (o.payload ->> 'rule_key') THEN
        RAISE EXCEPTION 'proposals: an edit of proposal % keeps its rule key', o.id USING ERRCODE = 'check_violation';
    END IF;
    IF o.kind = 'mapping' THEN
        IF (NEW.payload -> 'candidates') IS DISTINCT FROM (o.payload -> 'candidates') THEN
            RAISE EXCEPTION 'proposals: an edit of mapping proposal % keeps its candidates', o.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF COALESCE(jsonb_typeof(NEW.payload -> 'recommended') = 'string', false)
           AND NOT COALESCE(jsonb_typeof(NEW.payload -> 'candidates') = 'array'
                            AND (NEW.payload -> 'candidates') ? (NEW.payload ->> 'recommended'), false) THEN
            RAISE EXCEPTION 'proposals: an edit of mapping proposal % recommends %, which is not a candidate', o.id,
                NEW.payload ->> 'recommended' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF o.kind = 'glossary_element' THEN
        old_options := CASE WHEN jsonb_typeof(o.payload -> 'options') = 'array' THEN o.payload -> 'options'
                            ELSE '[]'::jsonb END;
        new_options := CASE WHEN jsonb_typeof(NEW.payload -> 'options') = 'array' THEN NEW.payload -> 'options'
                            ELSE '[]'::jsonb END;
        n := jsonb_array_length(old_options);
        IF n <> jsonb_array_length(new_options) THEN
            RAISE EXCEPTION 'proposals: an edit of glossary proposal % keeps the number of options', o.id
                USING ERRCODE = 'check_violation';
        END IF;
        FOR i IN 0 .. n - 1 LOOP
            IF (old_options -> i ->> 'kind') IS DISTINCT FROM (new_options -> i ->> 'kind')
               OR (old_options -> i ->> 'key') IS DISTINCT FROM (new_options -> i ->> 'key')
               OR (old_options -> i ->> 'alias') IS DISTINCT FROM (new_options -> i ->> 'alias') THEN
                RAISE EXCEPTION 'proposals: an edit of glossary proposal % keeps the kind and key of each option', o.id
                    USING ERRCODE = 'check_violation';
            END IF;
        END LOOP;
    END IF;
    -- Before the new row reaches the unique indexes: the old one stops being pending (see the module docstring).
    UPDATE proposals SET status = 'superseded', decided_at = clock_timestamp() WHERE id = o.id;
    RETURN NEW;
END $$;
"""

EDIT_FUNCTION = """
CREATE FUNCTION amaze_edit_proposal(old_id uuid, new_payload jsonb, editor text) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    o proposals%ROWTYPE;
    new_id uuid;
BEGIN
    IF editor IS NULL OR length(btrim(editor)) = 0 THEN
        RAISE EXCEPTION 'amaze_edit_proposal: who edited proposal % must be named', old_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO o FROM proposals WHERE id = old_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'amaze_edit_proposal: proposal % does not exist', old_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, requirement_id, feed_id,
                           edited_from, edited_by)
    VALUES (o.proposal_log_id, o.kind, new_payload, o.autonomy, o.required_approver_roles, o.requirement_id,
            o.feed_id, o.id, editor)
    RETURNING id INTO new_id;
    RETURN new_id;
END $$;
"""

SUPERSEDED_HAS_AN_EDIT = """
CREATE FUNCTION amaze_require_edit_of_superseded() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM proposals WHERE edited_from = NEW.id) THEN
        RAISE EXCEPTION 'proposals: proposal % was superseded with no edit', NEW.id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END $$;
"""

NO_APPROVAL_FOR_SUPERSEDED = """
CREATE FUNCTION amaze_refuse_superseded_approval() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_status text;
BEGIN
    SELECT status INTO p_status FROM proposals WHERE id = NEW.proposal_id;
    IF p_status IS NOT DISTINCT FROM 'superseded' THEN
        RAISE EXCEPTION 'approvals: proposal % is superseded by an edit and cannot be decided', NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def upgrade() -> None:
    # ---- proposals: edited_from, edited_by, superseded
    op.add_column("proposals", sa.Column("edited_from", sa.Uuid(), nullable=True))
    op.add_column("proposals", sa.Column("edited_by", sa.Text(), nullable=True))
    op.create_foreign_key(op.f("fk_proposals_edited_from_proposals"), "proposals", "proposals", ["edited_from"], ["id"])
    op.create_check_constraint(op.f("ck_proposals_edited_from_and_by_together"), "proposals",
                               "(edited_from IS NULL) = (edited_by IS NULL)")
    op.create_check_constraint(op.f("ck_proposals_edited_by"), "proposals",
                               "edited_by IS NULL OR length(btrim(edited_by)) > 0")
    op.create_index("uq_proposals_edited_from", "proposals", ["edited_from"], unique=True,
                    postgresql_where=sa.text("edited_from IS NOT NULL"))
    op.drop_constraint(op.f("ck_proposals_status"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_status"), "proposals",
                               "status IN ('pending', 'approved', 'rejected', 'superseded')")
    op.execute(PROPOSAL_CHANGE_0024)
    op.execute(PROPOSAL_EDIT_GUARD)
    op.execute("CREATE TRIGGER trg_proposals_edit BEFORE INSERT ON proposals FOR EACH ROW "
               "WHEN (NEW.edited_from IS NOT NULL) EXECUTE FUNCTION amaze_guard_proposal_edit()")
    op.execute(EDIT_FUNCTION)
    op.execute(SUPERSEDED_HAS_AN_EDIT)
    op.execute("CREATE CONSTRAINT TRIGGER trg_proposals_superseded_has_an_edit AFTER UPDATE ON proposals "
               "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
               "WHEN (NEW.status = 'superseded' AND OLD.status IS DISTINCT FROM 'superseded') "
               "EXECUTE FUNCTION amaze_require_edit_of_superseded()")
    op.execute(NO_APPROVAL_FOR_SUPERSEDED)
    op.execute("CREATE TRIGGER trg_approvals_not_superseded BEFORE INSERT ON approvals FOR EACH ROW "
               "EXECUTE FUNCTION amaze_refuse_superseded_approval()")

    # ---- onboarding_sessions
    op.execute(VALID_STEP_STATES)
    op.create_table(
        "onboarding_sessions",
        _id(),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("current_step", sa.Text(), nullable=False),
        sa.Column("step_states", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.Column("model_calls", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_onboarding_sessions")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_onboarding_sessions_feed_id_feeds")),
        sa.UniqueConstraint("feed_id", name=op.f("uq_onboarding_sessions_feed_id")),
        sa.CheckConstraint(_in("current_step", STEPS), name=op.f("ck_onboarding_sessions_current_step")),
        sa.CheckConstraint("amaze_valid_step_states(step_states)", name=op.f("ck_onboarding_sessions_step_states")),
        sa.CheckConstraint(_in("status", SESSION_STATUSES), name=op.f("ck_onboarding_sessions_status")),
        sa.CheckConstraint("length(btrim(created_by)) > 0", name=op.f("ck_onboarding_sessions_created_by")),
        sa.CheckConstraint("model_calls >= 0", name=op.f("ck_onboarding_sessions_model_calls")),
    )
    op.execute(SESSION_GUARD)
    op.execute("CREATE TRIGGER trg_onboarding_sessions_a_guard BEFORE UPDATE OR DELETE ON onboarding_sessions "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_onboarding_session()")
    op.execute("CREATE TRIGGER trg_onboarding_sessions_no_truncate BEFORE TRUNCATE ON onboarding_sessions "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_truncate()")

    # ---- onboarding_events
    op.create_table(
        "onboarding_events",
        _id(),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_onboarding_events")),
        sa.ForeignKeyConstraint(["session_id"], ["onboarding_sessions.id"],
                                name=op.f("fk_onboarding_events_session_id_onboarding_sessions")),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"],
                                name=op.f("fk_onboarding_events_proposal_id_proposals")),
        sa.CheckConstraint(_in("step", STEPS), name=op.f("ck_onboarding_events_step")),
        sa.CheckConstraint(_in("event", EVENTS), name=op.f("ck_onboarding_events_event")),
        sa.CheckConstraint("length(btrim(actor)) > 0", name=op.f("ck_onboarding_events_actor")),
        sa.CheckConstraint("jsonb_typeof(detail) = 'object'", name=op.f("ck_onboarding_events_detail_is_an_object")),
    )
    op.create_index("ix_onboarding_events_session_id_at", "onboarding_events", ["session_id", "at"])
    op.execute("CREATE TRIGGER trg_onboarding_events_append_only BEFORE UPDATE OR DELETE ON onboarding_events "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_onboarding_events_no_truncate BEFORE TRUNCATE ON onboarding_events "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()")

    # ---- onboarding_jobs
    op.create_table(
        "onboarding_jobs",
        _id(),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_onboarding_jobs")),
        sa.ForeignKeyConstraint(["session_id"], ["onboarding_sessions.id"],
                                name=op.f("fk_onboarding_jobs_session_id_onboarding_sessions")),
        sa.CheckConstraint(_in("step", STEPS), name=op.f("ck_onboarding_jobs_step")),
        sa.CheckConstraint(_in("status", JOB_STATUSES), name=op.f("ck_onboarding_jobs_status")),
        sa.CheckConstraint("attempt >= 0", name=op.f("ck_onboarding_jobs_attempt")),
        sa.CheckConstraint("status <> 'running' OR lease_until IS NOT NULL",
                           name=op.f("ck_onboarding_jobs_running_has_a_lease")),
        sa.CheckConstraint("(status = 'failed') = (error IS NOT NULL)",
                           name=op.f("ck_onboarding_jobs_failed_has_an_error")),
        sa.CheckConstraint("(status IN ('done', 'failed', 'discarded')) = (finished_at IS NOT NULL)",
                           name=op.f("ck_onboarding_jobs_finished_at_matches_status")),
    )
    op.create_index("ix_onboarding_jobs_session_id", "onboarding_jobs", ["session_id"])
    op.create_index("uq_onboarding_jobs_one_active_per_session", "onboarding_jobs", ["session_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.execute(JOB_GUARD)
    op.execute("CREATE TRIGGER trg_onboarding_jobs_a_guard BEFORE INSERT OR UPDATE OR DELETE ON onboarding_jobs "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_onboarding_job()")
    op.execute("CREATE TRIGGER trg_onboarding_jobs_no_truncate BEFORE TRUNCATE ON onboarding_jobs "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_truncate()")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM proposals WHERE status = 'superseded' OR edited_from IS NOT NULL)
               OR EXISTS (SELECT 1 FROM onboarding_sessions) THEN
                RAISE EXCEPTION 'migration 0024 cannot be downgraded: there are superseded or edited proposals or '
                                'onboarding sessions, which 0023 cannot hold';
            END IF;
        END $$;
        """
    )
    op.drop_table("onboarding_jobs")  # its triggers and indexes go with it
    op.drop_table("onboarding_events")
    op.drop_table("onboarding_sessions")
    op.execute("DROP FUNCTION amaze_guard_onboarding_job()")
    op.execute("DROP FUNCTION amaze_guard_onboarding_session()")
    op.execute("DROP FUNCTION amaze_valid_step_states(jsonb)")

    op.execute("DROP TRIGGER trg_approvals_not_superseded ON approvals")
    op.execute("DROP FUNCTION amaze_refuse_superseded_approval()")
    op.execute("DROP TRIGGER trg_proposals_superseded_has_an_edit ON proposals")
    op.execute("DROP FUNCTION amaze_require_edit_of_superseded()")
    op.execute("DROP FUNCTION amaze_edit_proposal(uuid, jsonb, text)")
    op.execute("DROP TRIGGER trg_proposals_edit ON proposals")
    op.execute("DROP FUNCTION amaze_guard_proposal_edit()")
    op.execute(PROPOSAL_CHANGE_0016)
    op.drop_constraint(op.f("ck_proposals_status"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_status"), "proposals", "status IN ('pending', 'approved', 'rejected')")
    op.drop_index("uq_proposals_edited_from", table_name="proposals")
    op.drop_constraint(op.f("ck_proposals_edited_by"), "proposals", type_="check")
    op.drop_constraint(op.f("ck_proposals_edited_from_and_by_together"), "proposals", type_="check")
    op.drop_constraint(op.f("fk_proposals_edited_from_proposals"), "proposals", type_="foreignkey")
    op.drop_column("proposals", "edited_by")
    op.drop_column("proposals", "edited_from")
