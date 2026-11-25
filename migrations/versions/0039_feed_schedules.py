"""feed_schedules and delivery_alerts: when a feed is expected, and who was told it did not come

Revision ID: 0039
Revises: 0038

Phase 7.21, T1 (plan: docs/plans/phase-7.21.md, sections 2 and 3). A schedule is a person's setting, not an agent's proposal;
rule 7 holds: a change is a new version, never an edit in place.

- feed_schedules: kind one_time | recurring, frequency (daily, business_days, weekly, monthly), weekday (0 Monday .. 6), day
  of month ('1'..'28' or 'last'), expected time, grace minutes, timezone, notify_owner, decided_by. The checks fit the shape to
  the kind and frequency. trg_feed_schedules_a_guard needs version = the feed's previous version + 1 (the first is 1).
  Append-only (UPDATE, DELETE, TRUNCATE refused; a DELETE only while amaze_delete_feed() deletes the row's own feed).
- delivery_alerts: one row per feed and due instant, so the owner is told once that an expected delivery did not come.
  Append-only in the same way.
- amaze_delete_feed() (0027, last patched by 0037) also deletes the feed's alerts and schedules.

No existing guard is loosened. Downgrade stops, naming 0039, while a schedule or an alert exists. PostgreSQL 14 features only.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

HERE = Path(__file__).resolve().parent


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"m{name}", next(HERE.glob(f"{name}_*.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _delete_function_0037() -> str:
    return _module("0037")._delete_function_0037()


def _delete_function_0039() -> str:
    m27 = _module("0027")
    block = (m27._delete_block("delivery_alerts", "feed_id = p_feed", "delivery_alerts")
             + m27._delete_block("feed_schedules", "feed_id = p_feed", "feed_schedules"))
    text = _delete_function_0037()
    needle = "    DELETE FROM standard_records WHERE"
    assert text.count(needle) == 1, "0039 expected exactly one standard_records delete block"
    return text.replace(needle, block + needle)


SCHEDULE_GUARD = """
CREATE FUNCTION amaze_guard_feed_schedule() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    last_version integer;
BEGIN
    SELECT COALESCE(max(version), 0) INTO last_version FROM feed_schedules WHERE feed_id = NEW.feed_id;
    IF NEW.version <> last_version + 1 THEN
        RAISE EXCEPTION 'feed schedule: version % is not the next version (%)', NEW.version, last_version + 1
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def _checks() -> list[sa.CheckConstraint]:
    def c(name: str, sql: str) -> sa.CheckConstraint:
        return sa.CheckConstraint(sql, name=op.f(f"ck_feed_schedules_{name}"))

    return [
        c("version", "version >= 1"),
        c("kind", "kind IN ('one_time', 'recurring')"),
        c("frequency", "frequency IS NULL OR frequency IN ('daily', 'business_days', 'weekly', 'monthly')"),
        c("weekday", "weekday IS NULL OR weekday BETWEEN 0 AND 6"),
        c("day_of_month", "day_of_month IS NULL OR day_of_month ~ '^([1-9]|1[0-9]|2[0-8]|last)$'"),
        c("grace_minutes", "grace_minutes BETWEEN 0 AND 1440"),
        c("timezone", "length(btrim(timezone)) > 0"),
        c("decided_by", "length(btrim(decided_by)) > 0"),
        c("one_time_has_no_schedule", "kind <> 'one_time' OR (frequency IS NULL AND weekday IS NULL "
                                      "AND day_of_month IS NULL AND expected_time IS NULL)"),
        c("recurring_has_frequency_and_time", "kind <> 'recurring' OR (frequency IS NOT NULL AND expected_time IS NOT NULL)"),
        c("weekly_has_weekday", "frequency IS DISTINCT FROM 'weekly' OR weekday IS NOT NULL"),
        c("weekday_only_weekly", "weekday IS NULL OR frequency IS NOT DISTINCT FROM 'weekly'"),
        c("monthly_has_day", "frequency IS DISTINCT FROM 'monthly' OR day_of_month IS NOT NULL"),
        c("day_only_monthly", "day_of_month IS NULL OR frequency IS NOT DISTINCT FROM 'monthly'"),
    ]


def _id_and_created() -> list[sa.Column]:
    return [sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False)]


def _append_only(table: str) -> None:
    op.execute(f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute(f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_truncate()")


def upgrade() -> None:
    op.create_table(
        "feed_schedules",
        *_id_and_created(),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("frequency", sa.Text(), nullable=True),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("day_of_month", sa.Text(), nullable=True),
        sa.Column("expected_time", sa.Time(), nullable=True),
        sa.Column("grace_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("timezone", sa.Text(), server_default="UTC", nullable=False),
        sa.Column("notify_owner", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_schedules")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_feed_schedules_feed_id_feeds")),
        sa.UniqueConstraint("feed_id", "version", name=op.f("uq_feed_schedules_feed_id_version")),
        *_checks(),
    )
    op.execute(SCHEDULE_GUARD)
    op.execute("CREATE TRIGGER trg_feed_schedules_a_guard BEFORE INSERT ON feed_schedules FOR EACH ROW "
               "EXECUTE FUNCTION amaze_guard_feed_schedule()")
    _append_only("feed_schedules")

    op.create_table(
        "delivery_alerts",
        *_id_and_created(),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delivery_alerts")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_delivery_alerts_feed_id_feeds")),
        sa.UniqueConstraint("feed_id", "due_at", name=op.f("uq_delivery_alerts_feed_id_due_at")),
        sa.CheckConstraint("schedule_version >= 1", name=op.f("ck_delivery_alerts_schedule_version")),
    )
    _append_only("delivery_alerts")
    op.execute(_delete_function_0039())


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM feed_schedules) OR EXISTS (SELECT 1 FROM delivery_alerts) THEN
                RAISE EXCEPTION 'migration 0039 cannot be downgraded: there are feed schedules or delivery alerts, '
                                'which 0038 cannot hold';
            END IF;
        END $$;
        """)
    op.execute(_delete_function_0037())
    op.drop_table("delivery_alerts")
    op.drop_table("feed_schedules")
    op.execute("DROP FUNCTION amaze_guard_feed_schedule()")
