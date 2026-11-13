"""requirements, feeds, mappings, rules, loads, products

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-13

Written across phase 1 tasks 7 to 9. Status lists are copied here on purpose: a migration must not
change when code changes; later phases add statuses with their own migrations.
PostgreSQL 14 features only.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

REQUIREMENT_STATUSES = ("defined", "onboarding", "ready", "access_restricted", "rejected")
FEED_STAGES = ("candidate", "registered", "rejected")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _created_at() -> sa.Column:
    # clock_timestamp(), not now(): rows inserted in one transaction keep their order ("newest first").
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")
    )


def upgrade() -> None:
    # ---- task 7: requirements and feeds
    op.create_table(
        "requirements",
        _id(),
        _created_at(),
        sa.Column("requester_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="defined"),
        sa.PrimaryKeyConstraint("id", name="pk_requirements"),
        sa.CheckConstraint(_in("status", REQUIREMENT_STATUSES), name="ck_requirements_status"),
        sa.CheckConstraint(
            "length(btrim(requester_id)) > 0 AND length(btrim(title)) > 0", name="ck_requirements_text_present"
        ),
    )
    op.create_table(
        "feeds",
        _id(),
        _created_at(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False, server_default="candidate"),
        sa.Column("requirement_id", sa.Uuid(), nullable=True),
        sa.Column("owner_id", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_feeds"),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"], name="fk_feeds_requirement_id_requirements"),
        sa.UniqueConstraint("code", name="uq_feeds_code"),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]*$'", name="ck_feeds_code_format"),
        sa.CheckConstraint(_in("stage", FEED_STAGES), name="ck_feeds_stage"),
        sa.CheckConstraint("stage <> 'registered' OR owner_id IS NOT NULL", name="ck_feeds_registered_has_owner"),
        sa.CheckConstraint(
            "(stage = 'registered') = (registered_at IS NOT NULL)", name="ck_feeds_registered_at_matches_stage"
        ),
    )
    op.create_foreign_key(
        "fk_proposals_requirement_id_requirements", "proposals", "requirements", ["requirement_id"], ["id"]
    )
    op.create_foreign_key("fk_proposals_feed_id_feeds", "proposals", "feeds", ["feed_id"], ["id"])
    # proposals are listed newest first; 0002 used now(), which is the same for a whole transaction.
    op.alter_column("proposals", "created_at", server_default=sa.text("clock_timestamp()"))

    # ---- task 8: mappings and rules (versioned, append-only, only from approved proposals)
    op.create_table(
        "mappings",
        _id(),
        _created_at(),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("source_field", sa.Text(), nullable=False),
        sa.Column("element_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_mappings"),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name="fk_mappings_feed_id_feeds"),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], name="fk_mappings_proposal_id_proposals"),
        sa.UniqueConstraint("feed_id", "source_field", "version", name="uq_mappings_feed_id_source_field_version"),
        sa.CheckConstraint("length(btrim(source_field)) > 0", name="ck_mappings_source_field"),
        sa.CheckConstraint("version >= 1", name="ck_mappings_version"),
    )
    op.create_table(
        "rules",
        _id(),
        _created_at(),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("rule_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rules"),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name="fk_rules_feed_id_feeds"),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], name="fk_rules_proposal_id_proposals"),
        sa.UniqueConstraint("feed_id", "rule_key", "version", name="uq_rules_feed_id_rule_key_version"),
        sa.CheckConstraint("rule_key ~ '^[a-z][a-z0-9_]*$'", name="ck_rules_rule_key_format"),
        sa.CheckConstraint("length(btrim(sql)) > 0 AND length(btrim(description)) > 0", name="ck_rules_text_present"),
        sa.CheckConstraint("version >= 1", name="ck_rules_version"),
    )
    for table in ("mappings", "rules"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()"
        )

    # A glossary element has several version rows, so element_key cannot be a foreign key. This trigger
    # checks it on insert. That is enough: mappings are never updated and glossary rows never deleted.
    op.execute(
        """
        CREATE FUNCTION amaze_require_glossary_key() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM glossary_elements WHERE key = NEW.element_key) THEN
                RAISE EXCEPTION 'element_key % is not in the glossary', NEW.element_key
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            RETURN NEW;
        END $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_mappings_glossary_key BEFORE INSERT ON mappings "
        "FOR EACH ROW EXECUTE FUNCTION amaze_require_glossary_key()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_mappings_glossary_key ON mappings")
    op.execute("DROP FUNCTION amaze_require_glossary_key()")
    for table in ("rules", "mappings"):
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.drop_table("rules")
    op.drop_table("mappings")
    op.alter_column("proposals", "created_at", server_default=sa.text("now()"))
    op.drop_constraint("fk_proposals_feed_id_feeds", "proposals", type_="foreignkey")
    op.drop_constraint("fk_proposals_requirement_id_requirements", "proposals", type_="foreignkey")
    op.drop_table("feeds")
    op.drop_table("requirements")
