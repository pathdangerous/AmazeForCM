"""sourcing: requirement status source_selected, feed sources, source_choices, source_fetches

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-14

Phase 3a (phase 3 plan, section 6).
- requirements: the status list gains source_selected. The check constraint is dropped and created
  again; rows are kept. The downgrade stops while a requirement is source_selected, because the old
  list has no such status.
- feeds: source_code and connector, set together; a connector needs a source_ref; one feed per source.
- source_choices: which source a requirement was handed to. One per requirement. Append-only.
- source_fetches: every fetch of a feed's source, fetched (with its file) or source_down (with the
  reason). Append-only.

Constraint names use op.f(): the name is final. Value lists are copied here on purpose: a migration
must not change when code changes. PostgreSQL 14 features only.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

STATUSES_0007 = ("defined", "onboarding", "ready", "access_restricted", "rejected")
STATUSES_0008 = (*STATUSES_0007, "source_selected")
APPEND_ONLY = ("source_choices", "source_fetches")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")
    )


def upgrade() -> None:
    op.drop_constraint(op.f("ck_requirements_status"), "requirements", type_="check")
    op.create_check_constraint(op.f("ck_requirements_status"), "requirements", _in("status", STATUSES_0008))

    op.add_column("feeds", sa.Column("source_code", sa.Text(), nullable=True))
    op.add_column("feeds", sa.Column("connector", sa.Text(), nullable=True))
    op.create_unique_constraint(op.f("uq_feeds_source_code"), "feeds", ["source_code"])
    op.create_check_constraint(op.f("ck_feeds_connector"), "feeds", "connector IN ('local_file', 'http')")
    op.create_check_constraint(
        op.f("ck_feeds_source_and_connector_together"), "feeds", "(source_code IS NULL) = (connector IS NULL)"
    )
    op.create_check_constraint(
        op.f("ck_feeds_connector_has_source_ref"), "feeds", "connector IS NULL OR source_ref IS NOT NULL"
    )

    op.create_table(
        "source_choices",
        _id(),
        _created_at(),
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_choices")),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["requirements.id"], name=op.f("fk_source_choices_requirement_id_requirements")
        ),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_source_choices_feed_id_feeds")),
        sa.UniqueConstraint("requirement_id", name=op.f("uq_source_choices_requirement_id")),
        sa.CheckConstraint("basis IN ('known_source')", name=op.f("ck_source_choices_basis")),
    )
    op.create_index(op.f("ix_source_choices_feed_id"), "source_choices", ["feed_id"])

    op.create_table(
        "source_fetches",
        _id(),
        _created_at(),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("blob_ref", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_fetches")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_source_fetches_feed_id_feeds")),
        sa.CheckConstraint("outcome IN ('fetched', 'source_down')", name=op.f("ck_source_fetches_outcome")),
        sa.CheckConstraint(
            # IS NOT NULL is spelt out: a check that evaluates to NULL passes.
            "outcome <> 'fetched' OR (blob_ref IS NOT NULL AND size_bytes IS NOT NULL AND size_bytes > 0 "
            "AND detail IS NULL)",
            name=op.f("ck_source_fetches_fetched_has_a_file"),
        ),
        sa.CheckConstraint(
            "outcome <> 'source_down' OR (detail IS NOT NULL AND length(btrim(detail)) > 0 "
            "AND blob_ref IS NULL AND size_bytes IS NULL)",
            name=op.f("ck_source_fetches_down_has_a_reason"),
        ),
    )
    op.create_index(op.f("ix_source_fetches_feed_id"), "source_fetches", ["feed_id"])

    # Append-only: UPDATE, DELETE and TRUNCATE refused (function from 0002).
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()"
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM requirements WHERE status = 'source_selected') THEN
                RAISE EXCEPTION 'migration 0008 cannot be downgraded: a requirement has status source_selected, '
                                'which 0007 does not allow';
            END IF;
        END $$;
        """
    )
    # Dropping a table drops its triggers and indexes.
    op.drop_table("source_fetches")
    op.drop_table("source_choices")
    op.drop_constraint(op.f("ck_feeds_connector_has_source_ref"), "feeds", type_="check")
    op.drop_constraint(op.f("ck_feeds_source_and_connector_together"), "feeds", type_="check")
    op.drop_constraint(op.f("ck_feeds_connector"), "feeds", type_="check")
    op.drop_constraint(op.f("uq_feeds_source_code"), "feeds", type_="unique")
    op.drop_column("feeds", "connector")
    op.drop_column("feeds", "source_code")
    op.drop_constraint(op.f("ck_requirements_status"), "requirements", type_="check")
    op.create_check_constraint(op.f("ck_requirements_status"), "requirements", _in("status", STATUSES_0007))
