"""upload_sources: an uploaded file is a source of its own; feeds.connector accepts dropzone

Revision ID: 0021
Revises: 0020

Phase 7.8, stage S2 (task 2.4; plan assumptions 16 and 17). An operator uploads a file with no consumer request;
Amaze keeps the original, writes a canonical CSV as the feed's fetched file and creates a candidate feed whose
connector is `dropzone` (the connector itself comes with task 4.4: the daily job leaves such feeds alone).

- feeds.connector: the CHECK gains 'dropzone'.
- upload_sources: one row per uploaded feed (code `upl_<feed code>`), append-only (UPDATE, DELETE and TRUNCATE
  refused with the function from 0002), the conditions the operator typed, the blob keys of the original and the
  manifest, and how the file was read. A BEFORE INSERT guard: the feed's source_code equals the row's code and its
  connector is dropzone.

Downgrade stops, naming 0021, while an upload source or a dropzone feed exists (0020 holds neither), instead of
failing half-way. PostgreSQL 14 features only: plpgsql, jsonb functions, row and statement triggers.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

CONNECTORS_0020 = "connector IN ('local_file', 'http')"
CONNECTORS_0021 = "connector IN ('local_file', 'http', 'dropzone')"
BLANK_CHECKED = ("name", "provider", "description", "licence", "permitted_use", "redistribution", "original_name",
                 "original_ref", "manifest_ref", "created_by")

# The feed must read this code through the dropzone. A feed that does not exist reads nothing: the same refusal.
GUARD = """
CREATE FUNCTION amaze_guard_upload_source() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    f_source text;
    f_connector text;
BEGIN
    SELECT source_code, connector INTO f_source, f_connector FROM feeds WHERE id = NEW.feed_id;
    IF f_source IS DISTINCT FROM NEW.code OR f_connector IS DISTINCT FROM 'dropzone' THEN
        RAISE EXCEPTION 'upload_sources: feed % does not read source % through the dropzone', NEW.feed_id, NEW.code USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.drop_constraint(op.f("ck_feeds_connector"), "feeds", type_="check")
    op.create_check_constraint(op.f("ck_feeds_connector"), "feeds", CONNECTORS_0021)

    op.create_table(
        "upload_sources",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("licence", sa.Text(), nullable=False),
        sa.Column("permitted_use", sa.Text(), nullable=False),
        sa.Column("redistribution", sa.Text(), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=False),
        sa.Column("original_ref", sa.Text(), nullable=False),
        sa.Column("manifest_ref", sa.Text(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("intake_options", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_upload_sources")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_upload_sources_feed_id_feeds")),
        sa.UniqueConstraint("code", name=op.f("uq_upload_sources_code")),
        sa.UniqueConstraint("feed_id", name=op.f("uq_upload_sources_feed_id")),
        sa.CheckConstraint("code ~ '^upl_[a-z][a-z0-9_]*$'", name=op.f("ck_upload_sources_code_format")),
        *(sa.CheckConstraint(f"length(btrim({column})) > 0", name=op.f(f"ck_upload_sources_{column}"))
          for column in BLANK_CHECKED),
        sa.CheckConstraint("jsonb_typeof(manifest) = 'object'", name=op.f("ck_upload_sources_manifest")),
        sa.CheckConstraint(
            "COALESCE(jsonb_typeof(intake_options) = 'object' "
            "AND jsonb_typeof(intake_options -> 'sheet') IN ('string', 'null') "
            "AND jsonb_typeof(intake_options -> 'header_row') = 'number' "
            "AND (intake_options ->> 'header_row') ~ '^[1-9][0-9]*$', false)",
            name=op.f("ck_upload_sources_intake_options")),
    )
    op.execute("CREATE TRIGGER trg_upload_sources_append_only BEFORE UPDATE OR DELETE ON upload_sources "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_upload_sources_no_truncate BEFORE TRUNCATE ON upload_sources "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute(GUARD)
    op.execute("CREATE TRIGGER trg_upload_sources_b_guard BEFORE INSERT ON upload_sources "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_upload_source()")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM upload_sources) OR EXISTS (SELECT 1 FROM feeds WHERE connector = 'dropzone') THEN
                RAISE EXCEPTION 'migration 0021 cannot be downgraded: there are upload sources or feeds read through '
                                'the dropzone, which 0020 cannot hold';
            END IF;
        END $$;
        """
    )
    op.drop_table("upload_sources")  # its triggers go with it
    op.execute("DROP FUNCTION amaze_guard_upload_source()")
    op.drop_constraint(op.f("ck_feeds_connector"), "feeds", type_="check")
    op.create_check_constraint(op.f("ck_feeds_connector"), "feeds", CONNECTORS_0020)
