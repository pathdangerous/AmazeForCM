"""pause and real delete of a feed

Revision ID: 0027
Revises: 0026

Phase 7.9 (plan: docs/plans/phase-7.9.md). Exception to rules 1 and 7 (CLAUDE.md): a feed may be hard-deleted only
through amaze_delete_feed(); the deletion is recorded in feed_deletions.

- feed_status_events (append-only): one row per pause or resume, with the reason and the person.
- feeds.stage gains 'paused'. amaze_guard_feed_stage() allows registered -> paused and paused -> registered, each only
  right after its own feed_status_events row (written in the same transaction); every other move is as in 0015. The two
  feeds CHECKs that tie the owner and registered_at to 'registered' now cover 'paused' too.
- feed_deletions (append-only): feed id (no FK: it outlives the feed), code, name, who, when, why, rows deleted per table.
- proposals.feed_id loses its FK to feeds: a proposal a glossary element was made from is kept (the request), and it
  must keep a feed id (0017 CHECK names_a_column), so it names a feed that feed_deletions records. A BEFORE INSERT
  trigger (amaze_require_feed_of_proposal) keeps the FK's protection for every new row: the feed must exist when the
  proposal is written.
- amaze_may_delete(table, old row): true only while amaze.deleting_feed names the row's own feed (its feed_id, or the
  feed of its parent row, or its id in the session temp table of log rows being deleted).
- The five functions that refuse a DELETE (amaze_refuse_update_delete, amaze_guard_glossary_run,
  amaze_guard_onboarding_job, amaze_guard_onboarding_session, amaze_guard_proposal_change) call it first. Nothing else
  about them changes. TRUNCATE is still refused.
- amaze_delete_feed(feed, by, reason): locks the feed row, refuses a feed with a received load (55006), sets
  amaze.deleting_feed for the transaction, deletes the feed's rows children first, keeps glossary elements and the
  proposals they were made from, writes feed_deletions and returns the per-table counts as jsonb.

Downgrade restores the six function bodies exactly, drops the two tables and the functions, restores the CHECKs and the
FK. It refuses (migration 0027 cannot be downgraded) while a feed is paused, a status event or a deletion is recorded,
or a proposal names a feed that no longer exists: those rows would be lost or would break the FK.
The function bodies are written out here on purpose: a migration must not change when code changes. PostgreSQL 14
features only: plpgsql, jsonb_exists, set_config, temp tables, GET DIAGNOSTICS.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

NEW_TABLES = ("feed_status_events", "feed_deletions")

# ---------------------------------------------------------------- the 0026 bodies, restored by downgrade

REFUSE_UPDATE_DELETE_OLD = """
CREATE OR REPLACE FUNCTION amaze_refuse_update_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: % refused', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation',
                      HINT = 'Configuration is versioned: insert a new version instead.';
        END $$;
"""

GLOSSARY_RUN_OLD = """
CREATE OR REPLACE FUNCTION amaze_guard_glossary_run() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    fixed text[] := ARRAY['status', 'attempt', 'lease_until', 'started_at', 'finished_at', 'proposal_log_id',
                          'results', 'error'];
    f_stage text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'glossary_runs: DELETE refused; a run is history' USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'queued' OR NEW.attempt IS DISTINCT FROM 0 THEN
            RAISE EXCEPTION 'glossary_runs: a new run is queued at attempt 0, not % at %', NEW.status, NEW.attempt
                USING ERRCODE = 'check_violation';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM feed_profiles WHERE id = NEW.profile_id AND feed_id = NEW.feed_id) THEN
            RAISE EXCEPTION 'glossary_runs: profile % is not a profile of feed %', NEW.profile_id, NEW.feed_id
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
        IF f_stage IS DISTINCT FROM 'registered' THEN
            RAISE EXCEPTION 'glossary_runs: feed % is %, not registered', NEW.feed_id, COALESCE(f_stage, 'missing')
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'done' OR OLD.status = 'failed' THEN
        RAISE EXCEPTION 'glossary_runs: run % is %, and a finished run never changes', OLD.id, OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (to_jsonb(NEW) - fixed - 'columns') IS DISTINCT FROM (to_jsonb(OLD) - fixed - 'columns') THEN
        RAISE EXCEPTION 'glossary_runs: the feed, profile, reason and requester of run % are fixed', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.status = 'queued' AND NEW.status = 'queued'
       AND (to_jsonb(NEW) - 'columns') IS NOT DISTINCT FROM (to_jsonb(OLD) - 'columns')
       AND NEW.columns @> OLD.columns THEN
        RETURN NEW;  -- another column sent back while the run waits
    END IF;
    IF NEW.columns IS DISTINCT FROM OLD.columns THEN
        RAISE EXCEPTION 'glossary_runs: the columns of run % only grow while it is queued', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (OLD.status = 'queued' AND NEW.status = 'running' AND NEW.attempt = OLD.attempt + 1)
       OR (OLD.status = 'running' AND NEW.status = 'running' AND NEW.attempt > OLD.attempt)
       OR (OLD.status = 'running' AND (NEW.status = 'done' OR NEW.status = 'failed') AND NEW.attempt = OLD.attempt) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'glossary_runs: run % cannot move from % (attempt %) to % (attempt %)', OLD.id, OLD.status,
        OLD.attempt, NEW.status, NEW.attempt USING ERRCODE = 'check_violation';
END $$;
"""

ONBOARDING_JOB_OLD = """
CREATE OR REPLACE FUNCTION amaze_guard_onboarding_job() RETURNS trigger LANGUAGE plpgsql AS $$
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

ONBOARDING_SESSION_OLD = """
CREATE OR REPLACE FUNCTION amaze_guard_onboarding_session() RETURNS trigger LANGUAGE plpgsql AS $$
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

PROPOSAL_CHANGE_OLD = """
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
       AND NEW.status IS DISTINCT FROM 'approved' AND NEW.status IS DISTINCT FROM 'rejected'
       AND NEW.status IS DISTINCT FROM 'superseded' THEN
        RAISE EXCEPTION 'proposals: a pending proposal moves only to approved or rejected, not %', NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;
"""

FEED_STAGE_OLD = """
CREATE OR REPLACE FUNCTION amaze_guard_feed_stage() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    r_owner text;
    r_signoff uuid;
    s_id uuid;
    s_outcome text;
    s_profile uuid;
    latest_profile uuid;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.stage IS DISTINCT FROM 'candidate' THEN
            RAISE EXCEPTION 'a new feed is a candidate, not %', NEW.stage USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    IF NEW.stage = OLD.stage THEN
        IF NEW.stage <> 'candidate' AND NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
            RAISE EXCEPTION 'the owner of a % feed is fixed', NEW.stage USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    SELECT id, outcome, profile_id INTO s_id, s_outcome, s_profile
      FROM feed_signoffs WHERE feed_id = NEW.id ORDER BY version DESC LIMIT 1;
    SELECT id INTO latest_profile FROM feed_profiles WHERE feed_id = NEW.id ORDER BY version DESC LIMIT 1;
    IF OLD.stage = 'candidate' AND NEW.stage = 'registered' THEN
        SELECT owner_id, signoff_id INTO r_owner, r_signoff
          FROM feed_registrations WHERE feed_id = NEW.id ORDER BY version DESC LIMIT 1;
        IF r_owner IS NULL OR r_owner IS DISTINCT FROM NEW.owner_id THEN
            RAISE EXCEPTION 'feed % needs a registration of owner % to be registered', NEW.id, NEW.owner_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF r_signoff IS DISTINCT FROM s_id THEN
            RAISE EXCEPTION 'feed % cannot be registered: its registration''s sign-off % is not the latest sign-off %',
                NEW.id, r_signoff, s_id USING ERRCODE = 'check_violation';
        END IF;
        IF s_outcome IS DISTINCT FROM 'meets' AND s_outcome IS DISTINCT FROM 'meets_with_gaps' THEN
            RAISE EXCEPTION 'feed % cannot be registered: sign-off % does not accept the feed (%)', NEW.id, s_id,
                s_outcome USING ERRCODE = 'check_violation';
        END IF;
        IF s_profile IS DISTINCT FROM latest_profile THEN
            RAISE EXCEPTION 'feed % cannot be registered: a newer profile waits for a sign-off', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    IF OLD.stage = 'candidate' AND NEW.stage = 'rejected' THEN
        IF s_outcome IS DISTINCT FROM 'rejected' THEN
            RAISE EXCEPTION 'feed % needs a rejected sign-off to be rejected', NEW.id USING ERRCODE = 'check_violation';
        END IF;
        IF s_profile IS DISTINCT FROM latest_profile THEN
            RAISE EXCEPTION 'feed % cannot be rejected: its rejected sign-off is not on the latest profile', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'feed % cannot move from % to %', NEW.id, OLD.stage, NEW.stage USING ERRCODE = 'check_violation';
END $$;
"""

# ---------------------------------------------------------------- the 0027 bodies


def _with_delete_exit(old_body: str, *, after: str) -> str:
    """The 0026 body with the delete exit added right after `after` (the line that opens the function's BEGIN)."""
    exit_ = ("    IF TG_OP = 'DELETE' AND amaze_may_delete(TG_TABLE_NAME::text, to_jsonb(OLD)) THEN\n"
             "        RETURN OLD;  -- amaze_delete_feed() is deleting this row's own feed (0027)\n"
             "    END IF;\n")
    assert old_body.count(after) == 1
    return old_body.replace(after, after + exit_)


REFUSE_UPDATE_DELETE_NEW = REFUSE_UPDATE_DELETE_OLD.replace(
    "        BEGIN\n",
    "        BEGIN\n"
    "            IF TG_OP = 'DELETE' AND amaze_may_delete(TG_TABLE_NAME::text, to_jsonb(OLD)) THEN\n"
    "                RETURN OLD;  -- amaze_delete_feed() is deleting this row's own feed (0027)\n"
    "            END IF;\n")
GLOSSARY_RUN_NEW = _with_delete_exit(GLOSSARY_RUN_OLD, after="    f_stage text;\nBEGIN\n")
ONBOARDING_JOB_NEW = _with_delete_exit(ONBOARDING_JOB_OLD, after="'error'];\nBEGIN\n")
ONBOARDING_SESSION_NEW = _with_delete_exit(ONBOARDING_SESSION_OLD, after="'model_calls'];\nBEGIN\n")
PROPOSAL_CHANGE_NEW = _with_delete_exit(PROPOSAL_CHANGE_OLD, after="AS $$\nBEGIN\n")

FEED_STAGE_NEW = FEED_STAGE_OLD.replace(
    "    latest_profile uuid;\nBEGIN\n",
    "    latest_profile uuid;\n    e_action text;\n    e_at timestamptz;\nBEGIN\n").replace(
    "    RAISE EXCEPTION 'feed % cannot move from % to %', NEW.id, OLD.stage, NEW.stage USING ERRCODE = 'check_violation';\n",
    """    IF (OLD.stage = 'registered' AND NEW.stage = 'paused') OR (OLD.stage = 'paused' AND NEW.stage = 'registered') THEN
        -- a pause or a resume is recorded first, in this transaction (feed_status_events)
        SELECT action, at INTO e_action, e_at FROM feed_status_events WHERE feed_id = NEW.id ORDER BY at DESC LIMIT 1;
        IF e_action IS DISTINCT FROM (CASE WHEN NEW.stage = 'paused' THEN 'pause' ELSE 'resume' END)
           OR e_at < transaction_timestamp() THEN
            RAISE EXCEPTION 'feed % cannot move from % to %: record the % in feed_status_events first', NEW.id,
                OLD.stage, NEW.stage, CASE WHEN NEW.stage = 'paused' THEN 'pause' ELSE 'resume' END
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'feed % cannot move from % to %', NEW.id, OLD.stage, NEW.stage USING ERRCODE = 'check_violation';
""")

PROPOSAL_FEED_EXISTS = """
CREATE FUNCTION amaze_require_feed_of_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.feed_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM feeds WHERE id = NEW.feed_id) THEN
        RAISE EXCEPTION 'proposals.feed_id: feed % does not exist', NEW.feed_id USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END $$;
"""

MAY_DELETE = """
CREATE FUNCTION amaze_may_delete(tbl text, old jsonb) RETURNS boolean LANGUAGE plpgsql STABLE AS $$
DECLARE
    target uuid;
    owner_feed uuid;
BEGIN
    BEGIN
        target := NULLIF(current_setting('amaze.deleting_feed', true), '')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        RETURN false;
    END;
    IF target IS NULL THEN
        RETURN false;
    END IF;
    IF jsonb_exists(old, 'feed_id') THEN
        RETURN (old ->> 'feed_id')::uuid IS NOT DISTINCT FROM target;
    ELSIF tbl = 'load_stages' THEN
        SELECT feed_id INTO owner_feed FROM loads WHERE id = (old ->> 'load_id')::uuid;
    ELSIF tbl IN ('product_elements', 'product_grants') THEN
        SELECT feed_id INTO owner_feed FROM products WHERE id = (old ->> 'product_id')::uuid;
    ELSIF tbl IN ('onboarding_events', 'onboarding_jobs') THEN
        SELECT feed_id INTO owner_feed FROM onboarding_sessions WHERE id = (old ->> 'session_id')::uuid;
    ELSIF tbl = 'approvals' THEN
        SELECT feed_id INTO owner_feed FROM proposals WHERE id = (old ->> 'proposal_id')::uuid;
    ELSIF tbl IN ('proposal_log', 'proposal_log_discards') THEN
        IF to_regclass('pg_temp.amaze_feed_logs') IS NULL THEN
            RETURN false;
        END IF;
        EXECUTE 'SELECT feed_id FROM pg_temp.amaze_feed_logs WHERE id = $1' INTO owner_feed
            USING (CASE WHEN tbl = 'proposal_log' THEN old ->> 'id' ELSE old ->> 'proposal_log_id' END)::uuid;
    ELSE
        RETURN false;
    END IF;
    RETURN owner_feed IS NOT DISTINCT FROM target;
END $$;
"""

# (table, where) in dependency order, children first; `loads` twice: scheduled loads, then go-lives, then test loads
DELETE_STEPS = (
    ("onboarding_events", "session_id IN (SELECT id FROM onboarding_sessions WHERE feed_id = p_feed)"),
    ("onboarding_jobs", "session_id IN (SELECT id FROM onboarding_sessions WHERE feed_id = p_feed)"),
    ("onboarding_sessions", "feed_id = p_feed"),
    ("glossary_runs", "feed_id = p_feed"),
    ("product_elements", "product_id IN (SELECT id FROM products WHERE feed_id = p_feed)"),
    ("product_grants", "product_id IN (SELECT id FROM products WHERE feed_id = p_feed)"),
    ("products", "feed_id = p_feed"),
    ("standard_records", "feed_id = p_feed"),
    ("load_stages", "load_id IN (SELECT id FROM loads WHERE feed_id = p_feed)"),
    ("load_certifications", "feed_id = p_feed"),
    ("loads", "feed_id = p_feed AND purpose = 'scheduled'"),
    ("feed_go_lives", "feed_id = p_feed"),
    ("loads", "feed_id = p_feed"),
    ("feed_registrations", "feed_id = p_feed"),
    ("feed_signoffs", "feed_id = p_feed"),
    ("mappings", "feed_id = p_feed"),
    ("rules", "feed_id = p_feed"),
    ("transformations", "feed_id = p_feed"),
    ("feed_key_policies", "feed_id = p_feed"),
    ("feed_profiles", "feed_id = p_feed"),
    ("source_fetches", "feed_id = p_feed"),
    ("upload_sources", "feed_id = p_feed"),
    ("source_choices", "feed_id = p_feed"),
    ("approvals", "proposal_id IN (SELECT id FROM proposals WHERE feed_id = p_feed "
                  "AND id NOT IN (SELECT id FROM amaze_feed_kept))"),
    ("proposals", "feed_id = p_feed AND id NOT IN (SELECT id FROM amaze_feed_kept)"),
)

AFTER_PROPOSALS = (
    # a log row stays if a proposal (a kept one, or another feed's) or a run still names it
    ("amaze_feed_logs", "id IN (SELECT proposal_log_id FROM proposals) OR id IN (SELECT proposal_log_id FROM glossary_runs)",
     None),
    ("proposal_log_discards", "proposal_log_id IN (SELECT id FROM amaze_feed_logs)", "proposal_log_discards"),
    ("proposal_log", "id IN (SELECT id FROM amaze_feed_logs)", "proposal_log"),
    ("feed_status_events", "feed_id = p_feed", "feed_status_events"),
)


def _delete_block(table: str, where: str, key: str | None) -> str:
    sql = f"    DELETE FROM {table} WHERE {where};\n"
    if key is not None:
        sql += ("    GET DIAGNOSTICS n = ROW_COUNT;\n"
                f"    counts := jsonb_set(counts, ARRAY['{key}'], "
                f"to_jsonb(COALESCE((counts ->> '{key}')::bigint, 0) + n));\n")
    return sql


def _delete_feed_function() -> str:
    body = "".join(_delete_block(t, w, t) for t, w in DELETE_STEPS)
    body += "".join(_delete_block(t, w, k) for t, w, k in AFTER_PROPOSALS)
    body += _delete_block("feeds", "id = p_feed", "feeds")
    return f"""
CREATE FUNCTION amaze_delete_feed(p_feed uuid, p_by text, p_reason text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    f feeds%ROWTYPE;
    counts jsonb := '{{}}'::jsonb;
    n bigint;
BEGIN
    IF p_reason IS NULL OR btrim(p_reason) = '' THEN
        RAISE EXCEPTION 'a deletion needs a reason' USING ERRCODE = 'check_violation';
    END IF;
    IF p_by IS NULL OR btrim(p_by) = '' THEN
        RAISE EXCEPTION 'a deletion needs the person who deletes' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO f FROM feeds WHERE id = p_feed FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'feed % does not exist', p_feed USING ERRCODE = 'no_data_found';
    END IF;
    IF EXISTS (SELECT 1 FROM loads WHERE feed_id = p_feed AND status = 'received') THEN
        RAISE EXCEPTION 'feed % has a load in progress: wait for it to finish', p_feed USING ERRCODE = 'object_in_use';
    END IF;
    PERFORM set_config('amaze.deleting_feed', p_feed::text, true);
    DROP TABLE IF EXISTS pg_temp.amaze_feed_logs;
    DROP TABLE IF EXISTS pg_temp.amaze_feed_kept;
    CREATE TEMP TABLE amaze_feed_logs (id uuid PRIMARY KEY, feed_id uuid NOT NULL) ON COMMIT DROP;
    -- kept: the proposals a glossary element was made from, and the proposals they were edited from
    CREATE TEMP TABLE amaze_feed_kept ON COMMIT DROP AS
    WITH RECURSIVE k(id) AS (
        SELECT p.id FROM proposals p
         WHERE p.feed_id = p_feed AND p.id IN (SELECT proposal_id FROM glossary_elements WHERE proposal_id IS NOT NULL)
        UNION
        SELECT p.edited_from FROM proposals p JOIN k ON p.id = k.id WHERE p.edited_from IS NOT NULL)
    SELECT id FROM k;
    INSERT INTO amaze_feed_logs (id, feed_id)
        SELECT DISTINCT proposal_log_id, p_feed FROM proposals
         WHERE feed_id = p_feed AND id NOT IN (SELECT id FROM amaze_feed_kept)
        ON CONFLICT DO NOTHING;
    INSERT INTO amaze_feed_logs (id, feed_id)
        SELECT DISTINCT proposal_log_id, p_feed FROM glossary_runs
         WHERE feed_id = p_feed AND proposal_log_id IS NOT NULL  -- a run not yet answered has no log row
        ON CONFLICT DO NOTHING;
{body}    INSERT INTO feed_deletions (feed_id, code, name, "by", reason, rows_deleted)
        VALUES (p_feed, f.code, f.name, p_by, p_reason, counts);
    PERFORM set_config('amaze.deleting_feed', '', true);
    RETURN counts;
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "feed_status_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_status_events")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_feed_status_events_feed_id_feeds")),
        sa.CheckConstraint("action IN ('pause', 'resume')", name=op.f("ck_feed_status_events_action")),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_feed_status_events_reason")),
        sa.CheckConstraint("length(btrim(\"by\")) > 0", name=op.f("ck_feed_status_events_by")),
    )
    op.create_index("ix_feed_status_events_feed_id", "feed_status_events", ["feed_id"])
    op.create_table(
        "feed_deletions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("by", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("rows_deleted", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_deletions")),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_feed_deletions_reason")),
        sa.CheckConstraint("length(btrim(\"by\")) > 0", name=op.f("ck_feed_deletions_by")),
    )
    # the stage: paused; the two CHECKs that tie the owner and registered_at to a live feed cover it too
    for name in ("ck_feeds_stage", "ck_feeds_registered_has_owner", "ck_feeds_registered_at_matches_stage"):
        op.execute(f"ALTER TABLE feeds DROP CONSTRAINT {name}")
    op.execute("ALTER TABLE feeds ADD CONSTRAINT ck_feeds_stage "
               "CHECK (stage IN ('candidate', 'registered', 'paused', 'rejected'))")
    op.execute("ALTER TABLE feeds ADD CONSTRAINT ck_feeds_registered_has_owner "
               "CHECK (stage NOT IN ('registered', 'paused') OR owner_id IS NOT NULL)")
    op.execute("ALTER TABLE feeds ADD CONSTRAINT ck_feeds_registered_at_matches_stage "
               "CHECK ((stage IN ('registered', 'paused')) = (registered_at IS NOT NULL))")
    op.execute("ALTER TABLE proposals DROP CONSTRAINT fk_proposals_feed_id_feeds")
    # the FK's protection stays for every new row: a proposal names a feed that exists when it is written
    op.execute(PROPOSAL_FEED_EXISTS)
    op.execute("CREATE TRIGGER trg_proposals_feed_exists BEFORE INSERT ON proposals "
               "FOR EACH ROW EXECUTE FUNCTION amaze_require_feed_of_proposal()")

    op.execute(MAY_DELETE)
    for body in (REFUSE_UPDATE_DELETE_NEW, GLOSSARY_RUN_NEW, ONBOARDING_JOB_NEW, ONBOARDING_SESSION_NEW,
                 PROPOSAL_CHANGE_NEW, FEED_STAGE_NEW):
        op.execute(body)
    for table in NEW_TABLES:
        op.execute(f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
        op.execute(f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute(_delete_feed_function())


def downgrade() -> None:
    op.execute("""
DO $$
DECLARE
    paused bigint := (SELECT count(*) FROM feeds WHERE stage = 'paused');
    events bigint := (SELECT count(*) FROM feed_status_events);
    deletions bigint := (SELECT count(*) FROM feed_deletions);
    dangling bigint := (SELECT count(*) FROM proposals WHERE feed_id IS NOT NULL AND feed_id NOT IN (SELECT id FROM feeds));
BEGIN
    IF paused + events + deletions + dangling > 0 THEN
        RAISE EXCEPTION 'migration 0027 cannot be downgraded: % paused feed(s), % status event(s), % recorded deletion(s) '
            'and % proposal(s) of a deleted feed would be lost or would break the foreign key',
            paused, events, deletions, dangling;
    END IF;
END $$;
""")
    op.execute("DROP TRIGGER trg_proposals_feed_exists ON proposals")
    op.execute("DROP FUNCTION amaze_require_feed_of_proposal()")
    op.execute("DROP FUNCTION amaze_delete_feed(uuid, text, text)")
    for body in (REFUSE_UPDATE_DELETE_OLD, GLOSSARY_RUN_OLD, ONBOARDING_JOB_OLD, ONBOARDING_SESSION_OLD,
                 PROPOSAL_CHANGE_OLD, FEED_STAGE_OLD):
        op.execute(body)
    op.execute("DROP FUNCTION amaze_may_delete(text, jsonb)")
    op.drop_table("feed_deletions")
    op.drop_table("feed_status_events")
    for name in ("ck_feeds_stage", "ck_feeds_registered_has_owner", "ck_feeds_registered_at_matches_stage"):
        op.execute(f"ALTER TABLE feeds DROP CONSTRAINT {name}")
    op.execute("ALTER TABLE feeds ADD CONSTRAINT ck_feeds_stage CHECK (stage IN ('candidate', 'registered', 'rejected'))")
    op.execute("ALTER TABLE feeds ADD CONSTRAINT ck_feeds_registered_has_owner "
               "CHECK (stage <> 'registered' OR owner_id IS NOT NULL)")
    op.execute("ALTER TABLE feeds ADD CONSTRAINT ck_feeds_registered_at_matches_stage "
               "CHECK ((stage = 'registered') = (registered_at IS NOT NULL))")
    op.execute("ALTER TABLE proposals ADD CONSTRAINT fk_proposals_feed_id_feeds FOREIGN KEY (feed_id) REFERENCES feeds (id)")
