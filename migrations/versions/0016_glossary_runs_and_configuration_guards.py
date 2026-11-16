"""glossary runs; a configuration row matches its proposal; a proposal is decided once

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-16

Phase 5 plan, section 6.
- glossary_runs (new): one run of the GlossaryBuilderAgent for one feed. Operational state (not
  append-only), but its status moves one way only (trg_glossary_runs_a_status), it is never deleted and
  TRUNCATE is refused. One queued or running row per feed (uq_glossary_runs_one_active_per_feed).
- proposals: amaze_guard_proposal_change() (0015) gains the status rule: status changes only from
  pending, and only to approved or rejected; a decided proposal never changes again. TRUNCATE refused.
  One pending glossary_element or mapping proposal per feed and column (the payload's source_field).
- glossary_elements: one row per proposal; a non-seed row is one of its proposal's options, written by
  the proposal's glossary_owner approver (trg_glossary_elements_b_option).
- mappings: one row per proposal; a row matches its proposal: feed, source field, one of its candidates,
  the column's next version, a registered feed (trg_mappings_b_matches_proposal).

The 0005 triggers stay and run first (trg_..._approved_proposal sorts before trg_..._b_). Every check on
a nullable value spells NULL out. Function bodies are written out here on purpose, the 0015 one too (for
the downgrade). PostgreSQL 14 or older only: partial unique indexes on an expression, jsonb operators and
jsonb_array_elements, to_jsonb, plpgsql, statement-level TRUNCATE triggers.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

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

# The 0015 body, restored by downgrade.
PROPOSAL_CHANGE_0015 = """
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
    RETURN NEW;
END $$;
"""

REFUSE_TRUNCATE = """
CREATE FUNCTION amaze_refuse_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '%: TRUNCATE refused; its rows are decisions or run history', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END $$;
"""

GLOSSARY_OPTION = """
CREATE FUNCTION amaze_require_glossary_option() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_payload jsonb;
    latest glossary_elements%ROWTYPE;
    opt jsonb;
    el jsonb;
BEGIN
    IF NEW.created_by = 'seed' THEN
        -- the seed writes version 1 of a key that does not exist yet, with no proposal, and nothing else
        IF NEW.proposal_id IS NOT NULL OR NEW.version IS DISTINCT FROM 1
           OR EXISTS (SELECT 1 FROM glossary_elements WHERE key = NEW.key) THEN
            RAISE EXCEPTION 'glossary element % version %: a seed row is version 1 of a new key, with no proposal',
                NEW.key, NEW.version USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    SELECT payload INTO p_payload FROM proposals WHERE id = NEW.proposal_id;
    IF NOT EXISTS (SELECT 1 FROM approvals WHERE proposal_id = NEW.proposal_id AND role = 'glossary_owner'
                      AND decision = 'approved' AND decided_by = NEW.created_by) THEN
        RAISE EXCEPTION 'glossary element %: created_by % is not the glossary_owner who approved proposal %',
            NEW.key, NEW.created_by, NEW.proposal_id USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO latest FROM glossary_elements WHERE key = NEW.key ORDER BY version DESC LIMIT 1;
    FOR opt IN SELECT value FROM jsonb_array_elements(COALESCE(p_payload -> 'options', '[]'::jsonb)) LOOP
        IF opt ->> 'kind' = 'new_element' AND (opt ->> 'key') IS NOT DISTINCT FROM NEW.key THEN
            el := opt -> 'element';
            IF NEW.version = 1 AND latest.id IS NULL
               AND (el ->> 'key') IS NOT DISTINCT FROM NEW.key
               AND (el ->> 'name') IS NOT DISTINCT FROM NEW.name
               AND (el ->> 'domain') IS NOT DISTINCT FROM NEW.domain
               AND (el ->> 'data_type') IS NOT DISTINCT FROM NEW.data_type
               AND (el ->> 'definition') IS NOT DISTINCT FROM NEW.definition
               AND COALESCE(el -> 'aliases', '[]'::jsonb) IS NOT DISTINCT FROM to_jsonb(NEW.aliases) THEN
                RETURN NEW;
            END IF;
        ELSIF opt ->> 'kind' = 'alias' AND (opt ->> 'key') IS NOT DISTINCT FROM NEW.key
              AND (opt ->> 'alias') IS NOT NULL AND latest.id IS NOT NULL THEN
            IF NEW.version = latest.version + 1
               AND NEW.name IS NOT DISTINCT FROM latest.name
               AND NEW.domain IS NOT DISTINCT FROM latest.domain
               AND NEW.data_type IS NOT DISTINCT FROM latest.data_type
               AND NEW.definition IS NOT DISTINCT FROM latest.definition
               AND NEW.aliases IS NOT DISTINCT FROM (latest.aliases || ARRAY[opt ->> 'alias']) THEN
                RETURN NEW;
            END IF;
        END IF;
    END LOOP;
    RAISE EXCEPTION 'glossary element % version % is none of the options of proposal %', NEW.key, NEW.version,
        NEW.proposal_id USING ERRCODE = 'check_violation';
END $$;
"""

MAPPING_MATCHES = """
CREATE FUNCTION amaze_require_mapping_matches_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_feed uuid;
    p_payload jsonb;
    latest integer;
    f_stage text;
BEGIN
    SELECT feed_id, payload INTO p_feed, p_payload FROM proposals WHERE id = NEW.proposal_id;
    IF p_feed IS DISTINCT FROM NEW.feed_id THEN
        RAISE EXCEPTION 'mapping: proposal % is for feed %, not %', NEW.proposal_id, COALESCE(p_feed::text, 'no feed'),
            NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    IF (p_payload ->> 'source_field') IS DISTINCT FROM NEW.source_field THEN
        RAISE EXCEPTION 'mapping: proposal % is for column %, not %', NEW.proposal_id,
            COALESCE(p_payload ->> 'source_field', 'none'), NEW.source_field USING ERRCODE = 'check_violation';
    END IF;
    IF NOT COALESCE(jsonb_typeof(p_payload -> 'candidates') = 'array'
                    AND (p_payload -> 'candidates') ? NEW.element_key, false) THEN
        RAISE EXCEPTION 'mapping: % is not a candidate of proposal %', NEW.element_key, NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(version) INTO latest FROM mappings WHERE feed_id = NEW.feed_id AND source_field = NEW.source_field;
    IF NEW.version IS DISTINCT FROM COALESCE(latest, 0) + 1 THEN
        RAISE EXCEPTION 'mapping: version % of column % is not the next one (%)', NEW.version, NEW.source_field,
            COALESCE(latest, 0) + 1 USING ERRCODE = 'check_violation';
    END IF;
    SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
    IF f_stage IS DISTINCT FROM 'registered' THEN
        RAISE EXCEPTION 'mapping: feed % is %, not registered', NEW.feed_id, COALESCE(f_stage, 'missing')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

RUN_STATUS = """
CREATE FUNCTION amaze_guard_glossary_run() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    fixed text[] := ARRAY['status', 'attempt', 'lease_until', 'started_at', 'finished_at', 'proposal_log_id',
                          'results', 'error'];
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'glossary_runs: DELETE refused; a run is history' USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'queued' OR NEW.attempt IS DISTINCT FROM 0 THEN
            RAISE EXCEPTION 'glossary_runs: a new run is queued at attempt 0, not % at %', NEW.status, NEW.attempt
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


def upgrade() -> None:
    op.create_table(
        "glossary_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("columns", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposal_log_id", sa.Uuid(), nullable=True),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_glossary_runs")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_glossary_runs_feed_id_feeds")),
        sa.ForeignKeyConstraint(["profile_id"], ["feed_profiles.id"],
                                name=op.f("fk_glossary_runs_profile_id_feed_profiles")),
        sa.ForeignKeyConstraint(["proposal_log_id"], ["proposal_log.id"],
                                name=op.f("fk_glossary_runs_proposal_log_id_proposal_log")),
        sa.CheckConstraint("reason IN ('registered', 'requested', 'sent_back')", name=op.f("ck_glossary_runs_reason")),
        sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name=op.f("ck_glossary_runs_status")),
        sa.CheckConstraint("attempt >= 0", name=op.f("ck_glossary_runs_attempt")),
        sa.CheckConstraint("length(btrim(requested_by)) > 0", name=op.f("ck_glossary_runs_requested_by")),
        sa.CheckConstraint("status <> 'running' OR lease_until IS NOT NULL",
                           name=op.f("ck_glossary_runs_running_has_a_lease")),
        sa.CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name=op.f("ck_glossary_runs_failed_has_an_error")),
        sa.CheckConstraint("(status = 'done') = (results IS NOT NULL)", name=op.f("ck_glossary_runs_done_has_results")),
        sa.CheckConstraint("(status IN ('done', 'failed')) = (finished_at IS NOT NULL)",
                           name=op.f("ck_glossary_runs_finished_at_matches_status")),
        sa.CheckConstraint("proposal_log_id IS NULL OR status = 'done'",
                           name=op.f("ck_glossary_runs_call_only_when_done")),
    )
    op.create_index("ix_glossary_runs_feed_id", "glossary_runs", ["feed_id"])
    op.create_index("uq_glossary_runs_one_active_per_feed", "glossary_runs", ["feed_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.execute(RUN_STATUS)
    op.execute("CREATE TRIGGER trg_glossary_runs_a_status BEFORE INSERT OR UPDATE OR DELETE ON glossary_runs "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_glossary_run()")
    op.execute(REFUSE_TRUNCATE)
    for table in ("glossary_runs", "proposals"):
        op.execute(f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_truncate()")

    op.execute(PROPOSAL_CHANGE_0016)
    op.create_index("uq_proposals_one_pending_per_feed_column", "proposals",
                    ["kind", "feed_id", sa.text("(payload ->> 'source_field')")], unique=True,
                    postgresql_where=sa.text("status = 'pending' AND kind IN ('glossary_element', 'mapping')"))

    op.create_index("uq_glossary_elements_proposal_id", "glossary_elements", ["proposal_id"], unique=True,
                    postgresql_where=sa.text("proposal_id IS NOT NULL"))
    op.execute(GLOSSARY_OPTION)
    op.execute("CREATE TRIGGER trg_glossary_elements_b_option BEFORE INSERT ON glossary_elements "
               "FOR EACH ROW EXECUTE FUNCTION amaze_require_glossary_option()")

    op.create_index("uq_mappings_proposal_id", "mappings", ["proposal_id"], unique=True)
    op.execute(MAPPING_MATCHES)
    op.execute("CREATE TRIGGER trg_mappings_b_matches_proposal BEFORE INSERT ON mappings "
               "FOR EACH ROW EXECUTE FUNCTION amaze_require_mapping_matches_proposal()")


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM glossary_runs) THEN
                RAISE EXCEPTION 'migration 0016 cannot be downgraded: glossary_runs has rows';
            END IF;
        END $$;
    """)
    op.execute("DROP TRIGGER trg_mappings_b_matches_proposal ON mappings")
    op.execute("DROP FUNCTION amaze_require_mapping_matches_proposal()")
    op.drop_index("uq_mappings_proposal_id", table_name="mappings")
    op.execute("DROP TRIGGER trg_glossary_elements_b_option ON glossary_elements")
    op.execute("DROP FUNCTION amaze_require_glossary_option()")
    op.drop_index("uq_glossary_elements_proposal_id", table_name="glossary_elements")
    op.drop_index("uq_proposals_one_pending_per_feed_column", table_name="proposals")
    op.execute(PROPOSAL_CHANGE_0015)
    op.execute("DROP TRIGGER trg_proposals_no_truncate ON proposals")
    op.execute("DROP TRIGGER trg_glossary_runs_no_truncate ON glossary_runs")
    op.execute("DROP FUNCTION amaze_refuse_truncate()")
    op.execute("DROP TRIGGER trg_glossary_runs_a_status ON glossary_runs")
    op.execute("DROP FUNCTION amaze_guard_glossary_run()")
    op.drop_index("uq_glossary_runs_one_active_per_feed", table_name="glossary_runs")
    op.drop_index("ix_glossary_runs_feed_id", table_name="glossary_runs")
    op.drop_table("glossary_runs")
