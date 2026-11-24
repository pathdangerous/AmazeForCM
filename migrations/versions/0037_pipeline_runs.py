"""pipeline_runs: the code pipeline's runs, and a go-live that needs a passed one

Revision ID: 0037
Revises: 0036

Phase 7.17, T2 (plan: docs/plans/phase-7.17.md, 2.2). Rule 7 still holds: a run is evidence about generated code, it never
changes configuration.

- pipeline_runs: one row per run of the pipeline over a feed's generated code, keyed by the SHA-256 of the files (the hash
  a code version carries, 0030). `stages` is exactly four objects in order (commit, sql_parse, rule_check, test_load), each
  `{stage, status, log}`; the stages before the first failed one passed, the ones after it were skipped. Append-only
  (UPDATE, DELETE and TRUNCATE refused; a DELETE only while amaze_delete_feed() deletes the row's own feed).
  trg_pipeline_runs_guard recomputes the outcome from the stages (`passed`, or `failed_at_<first failed stage>`) and needs a
  commit hash exactly when the commit stage passed.
- trg_feed_go_lives_d_pipeline: every NEW go-live needs a passed run of its own feed whose code hash is the hash of the code
  version it pins. Earlier go-lives are not touched.
- amaze_delete_feed() (0027, last patched by 0034) also deletes the feed's runs.

Downgrade stops, naming 0037, while a run exists. PostgreSQL 14 features only.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

HERE = Path(__file__).resolve().parent


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"m{name}", next(HERE.glob(f"{name}_*.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _delete_function_0034() -> str:
    return _module("0034")._delete_function_0034()


def _delete_function_0037() -> str:
    block = _module("0027")._delete_block("pipeline_runs", "feed_id = p_feed", "pipeline_runs")
    text = _delete_function_0034()
    needle = "    DELETE FROM standard_records WHERE"
    assert text.count(needle) == 1, "0037 expected exactly one standard_records delete block"
    return text.replace(needle, block + needle)


RUN_GUARD = r"""
CREATE FUNCTION amaze_guard_pipeline_run() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    names text[] := ARRAY['commit', 'sql_parse', 'rule_check', 'test_load'];
    i integer;
    st text;
    failed_at integer := 0;
    expected text;
BEGIN
    IF jsonb_typeof(NEW.stages) IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.stages) <> 4 THEN
        RAISE EXCEPTION 'pipeline_runs: stages are exactly the four stages, in order' USING ERRCODE = 'check_violation';
    END IF;
    FOR i IN 1..4 LOOP
        IF NEW.stages -> (i - 1) ->> 'stage' IS DISTINCT FROM names[i]
           OR jsonb_typeof(NEW.stages -> (i - 1) -> 'log') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'pipeline_runs: stage % must be %, with a log', i, names[i] USING ERRCODE = 'check_violation';
        END IF;
        st := NEW.stages -> (i - 1) ->> 'status';
        IF st IS NULL OR st NOT IN ('passed', 'failed', 'skipped') THEN
            RAISE EXCEPTION 'pipeline_runs: the status of stage % is passed, failed or skipped, not %', names[i], st
                USING ERRCODE = 'check_violation';
        END IF;
        IF failed_at = 0 THEN
            IF st = 'failed' THEN
                failed_at := i;
            ELSIF st <> 'passed' THEN
                RAISE EXCEPTION 'pipeline_runs: stage % is % but no stage failed before it; stages before the first failure passed',
                    names[i], st USING ERRCODE = 'check_violation';
            END IF;
        ELSIF st <> 'skipped' THEN
            RAISE EXCEPTION 'pipeline_runs: stage % comes after the failure at % and must be skipped', names[i], names[failed_at]
                USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;
    expected := CASE WHEN failed_at = 0 THEN 'passed' ELSE 'failed_at_' || names[failed_at] END;
    IF NEW.outcome IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'pipeline_runs: the outcome is % from these stages, not %', expected, NEW.outcome
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.commit_hash IS NOT NULL) IS DISTINCT FROM (NEW.stages -> 0 ->> 'status' = 'passed') THEN
        RAISE EXCEPTION 'pipeline_runs: a run has a commit hash exactly when its commit stage passed'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

GO_LIVE_NEEDS_PIPELINE = """
CREATE FUNCTION amaze_go_live_needs_pipeline() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    c code_versions%ROWTYPE;
BEGIN
    SELECT * INTO c FROM code_versions WHERE id = NEW.code_version_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- trg_feed_go_lives_c_code_version has refused it already
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pipeline_runs r
                    WHERE r.feed_id = NEW.feed_id AND r.code_sha256 = c.sha256 AND r.outcome = 'passed') THEN
        RAISE EXCEPTION 'go-live: code version % has no passed pipeline run', c.version USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("code_sha256", sa.Text(), nullable=False),
        sa.Column("commit_hash", sa.Text(), nullable=True),
        sa.Column("commit_created", sa.Boolean(), nullable=False),
        sa.Column("stages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("run_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pipeline_runs")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_pipeline_runs_feed_id_feeds")),
        sa.CheckConstraint("code_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_pipeline_runs_code_sha256")),
        sa.CheckConstraint("commit_hash IS NULL OR commit_hash ~ '^[0-9a-f]{40,64}$'", name=op.f("ck_pipeline_runs_commit_hash")),
        sa.CheckConstraint("length(btrim(run_by)) > 0", name=op.f("ck_pipeline_runs_run_by")),
        sa.CheckConstraint("outcome IN ('passed', 'failed_at_commit', 'failed_at_sql_parse', 'failed_at_rule_check', "
                           "'failed_at_test_load')", name=op.f("ck_pipeline_runs_outcome")),
    )
    op.create_index("ix_pipeline_runs_feed_id_code_sha256", "pipeline_runs", ["feed_id", "code_sha256"])
    op.execute(RUN_GUARD)
    op.execute("CREATE TRIGGER trg_pipeline_runs_a_guard BEFORE INSERT ON pipeline_runs FOR EACH ROW "
               "EXECUTE FUNCTION amaze_guard_pipeline_run()")
    op.execute("CREATE TRIGGER trg_pipeline_runs_append_only BEFORE UPDATE OR DELETE ON pipeline_runs "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_pipeline_runs_no_truncate BEFORE TRUNCATE ON pipeline_runs "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_truncate()")
    op.execute(GO_LIVE_NEEDS_PIPELINE)
    op.execute("CREATE TRIGGER trg_feed_go_lives_d_pipeline BEFORE INSERT ON feed_go_lives FOR EACH ROW "
               "EXECUTE FUNCTION amaze_go_live_needs_pipeline()")
    op.execute(_delete_function_0037())


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pipeline_runs) THEN
                RAISE EXCEPTION 'migration 0037 cannot be downgraded: there are pipeline runs, which 0036 cannot hold';
            END IF;
        END $$;
        """)
    op.execute(_delete_function_0034())
    op.execute("DROP TRIGGER trg_feed_go_lives_d_pipeline ON feed_go_lives")
    op.execute("DROP FUNCTION amaze_go_live_needs_pipeline()")
    op.drop_table("pipeline_runs")
    op.execute("DROP FUNCTION amaze_guard_pipeline_run()")
