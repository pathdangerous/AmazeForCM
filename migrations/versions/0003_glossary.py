"""glossary_elements (versioned, append-only)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13

Rule 1 in the database: an element without a proposal must have been created by the seed.
Rule 7 in the database: UPDATE and DELETE are refused by the append-only trigger from 0002.
The data type list is copied here on purpose: a migration must not change when code changes.
PostgreSQL 14 features only.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

DATA_TYPES = ("string", "integer", "decimal", "date", "datetime", "boolean")


def upgrade() -> None:
    op.create_table(
        "glossary_elements",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("data_type", sa.Text(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_glossary_elements"),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], name="fk_glossary_elements_proposal_id_proposals"),
        sa.UniqueConstraint("key", "version", name="uq_glossary_elements_key_version"),
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name="ck_glossary_elements_key_format"),
        sa.CheckConstraint("domain ~ '^[a-z][a-z0-9_]*$'", name="ck_glossary_elements_domain_format"),
        sa.CheckConstraint("version >= 1", name="ck_glossary_elements_version"),
        sa.CheckConstraint(
            f"data_type IN ({', '.join(repr(t) for t in DATA_TYPES)})", name="ck_glossary_elements_data_type"
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0 AND length(btrim(definition)) > 0", name="ck_glossary_elements_text_present"
        ),
        sa.CheckConstraint(
            "(proposal_id IS NULL) = (created_by = 'seed')", name="ck_glossary_elements_proposal_unless_seed"
        ),
    )
    op.execute(
        "CREATE TRIGGER trg_glossary_elements_append_only BEFORE UPDATE OR DELETE ON glossary_elements "
        "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_glossary_elements_append_only ON glossary_elements")
    op.drop_table("glossary_elements")
