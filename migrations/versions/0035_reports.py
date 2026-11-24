"""reports: an append-only, versioned report per data owner or data steward; loads.rerun_by

Revision ID: 0035
Revises: 0034

Phase 7.15, T2 (plan: docs/plans/phase-7.15.md, 2.2 and 2.4).

- reports (new, append-only): one report of one subject (a data owner or a data steward), one kind (daily: one UTC day;
  monthly: one UTC calendar month) and one period, as a version. `numbers` is counted by code from the tables; the
  database sets `numbers_sha256` from it itself (a caller's value is overwritten) and refuses a version that is not the
  next for (kind, subject, role, period). The narrative is a separate, optional field: `narrative_status` is written (a
  narrative and the logged call that made it), refused (the narrative stated a figure the report does not hold: none is
  kept, the refusal is noted) or none. UPDATE, DELETE and TRUNCATE are refused. A report has no feed_id: a deleted feed
  leaves the reports that were true when they were made.
- loads.rerun_by (new, nullable): who started a rerun (the audit view shows it). Reruns made by 0034 have none. The rerun
  guard (0034) is replaced: rerun_by is set at insert, only with a rerun, and never changes.

Downgrade stops, naming 0035, while a report or a rerun_by exists. PostgreSQL 14 features only.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

HERE = Path(__file__).resolve().parent


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"m{name}", next(HERE.glob(f"{name}_*.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _swap(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"0035 expected exactly one: {old[:70]!r}"
    return text.replace(old, new)


REPORT_GUARD = """
CREATE FUNCTION amaze_guard_report() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected integer;
BEGIN
    NEW.numbers_sha256 := encode(sha256(convert_to(NEW.numbers::text, 'UTF8')), 'hex');
    SELECT coalesce(max(version), 0) + 1 INTO expected FROM reports
     WHERE kind = NEW.kind AND subject_id = NEW.subject_id AND subject_role = NEW.subject_role
       AND period_start = NEW.period_start;
    IF NEW.version IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'reports: the next version of the % report of % for % is %, not %', NEW.kind, NEW.subject_id,
            NEW.period_start, expected, NEW.version USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def _rerun_guard_0034() -> str:
    return _module("0034").LOAD_RERUN


def _rerun_guard_0035() -> str:
    text = _rerun_guard_0034().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    text = _swap(text, "           OR NEW.rerun_reason IS DISTINCT FROM OLD.rerun_reason THEN",
                 "           OR NEW.rerun_reason IS DISTINCT FROM OLD.rerun_reason\n"
                 "           OR NEW.rerun_by IS DISTINCT FROM OLD.rerun_by THEN")
    return _swap(text, "    IF NEW.rerun_of_load_id IS NULL THEN\n        RETURN NEW;\n    END IF;",
                 "    IF NEW.rerun_of_load_id IS NULL THEN\n"
                 "        IF NEW.rerun_by IS NOT NULL THEN\n"
                 "            RAISE EXCEPTION 'loads: load % names who reran it but is no rerun', NEW.id\n"
                 "                USING ERRCODE = 'check_violation';\n"
                 "        END IF;\n"
                 "        RETURN NEW;\n    END IF;")


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("subject_role", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("numbers", postgresql.JSONB(), nullable=False),
        sa.Column("numbers_sha256", sa.Text(), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("narrative_status", sa.Text(), nullable=False),
        sa.Column("narrative_note", sa.Text(), nullable=True),
        sa.Column("narrative_log_id", sa.Uuid(), nullable=True),
        sa.Column("generated_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        sa.ForeignKeyConstraint(["narrative_log_id"], ["proposal_log.id"],
                                name=op.f("fk_reports_narrative_log_id_proposal_log")),
        sa.UniqueConstraint("kind", "subject_id", "subject_role", "period_start", "version",
                            name=op.f("uq_reports_kind_subject_id_subject_role_period_start_version")),
        sa.CheckConstraint("kind IN ('daily', 'monthly')", name=op.f("ck_reports_kind")),
        sa.CheckConstraint("subject_role IN ('data_owner', 'data_steward')", name=op.f("ck_reports_subject_role")),
        sa.CheckConstraint("length(btrim(subject_id)) > 0 AND length(btrim(generated_by)) > 0",
                           name=op.f("ck_reports_named")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_reports_version")),
        sa.CheckConstraint("jsonb_typeof(numbers) = 'object'", name=op.f("ck_reports_numbers")),
        sa.CheckConstraint(
            "(kind = 'daily' AND period_end = period_start + 1) "
            "OR (kind = 'monthly' AND period_start = date_trunc('month', period_start)::date "
            "AND period_end = (period_start + interval '1 month')::date)", name=op.f("ck_reports_period_fits_kind")),
        sa.CheckConstraint("narrative_status IN ('written', 'refused', 'none')", name=op.f("ck_reports_narrative_status")),
        # IS NOT NULL is spelt out: a check that evaluates to NULL passes.
        sa.CheckConstraint(
            "(narrative_status = 'written' AND narrative IS NOT NULL AND length(btrim(narrative)) > 0 "
            "AND narrative_log_id IS NOT NULL) "
            "OR (narrative_status = 'refused' AND narrative IS NULL AND narrative_log_id IS NOT NULL "
            "AND narrative_note IS NOT NULL AND length(btrim(narrative_note)) > 0) "
            "OR (narrative_status = 'none' AND narrative IS NULL AND narrative_log_id IS NULL)",
            name=op.f("ck_reports_narrative_fits_status")),
    )
    op.create_index("ix_reports_subject", "reports", ["subject_id", "kind", "period_start"])
    op.execute(REPORT_GUARD)
    op.execute("CREATE TRIGGER trg_reports_a_guard BEFORE INSERT ON reports FOR EACH ROW "
               "EXECUTE FUNCTION amaze_guard_report()")
    op.execute("CREATE TRIGGER trg_reports_append_only BEFORE UPDATE OR DELETE ON reports "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_reports_no_truncate BEFORE TRUNCATE ON reports "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_truncate()")

    op.add_column("loads", sa.Column("rerun_by", sa.Text(), nullable=True))
    op.create_check_constraint(op.f("ck_loads_rerun_by_names_a_person"), "loads",
                               "rerun_by IS NULL OR (rerun_of_load_id IS NOT NULL AND length(btrim(rerun_by)) > 0)")
    op.execute(_rerun_guard_0035())


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM reports) OR EXISTS (SELECT 1 FROM loads WHERE rerun_by IS NOT NULL) THEN
                RAISE EXCEPTION 'migration 0035 cannot be downgraded: there are reports or reruns with their person, '
                                'which 0034 cannot hold';
            END IF;
        END $$;
        """)
    op.execute(_rerun_guard_0034().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.drop_constraint(op.f("ck_loads_rerun_by_names_a_person"), "loads", type_="check")
    op.drop_column("loads", "rerun_by")
    op.drop_table("reports")
    op.execute("DROP FUNCTION amaze_guard_report()")
