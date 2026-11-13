"""consumption: product owner, visibility, elements and grants; requirement elements and terms; request log

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-14

Written across phase 2 tasks 1 to 3 (phase 2 plan, section 6).

Task 1: products gains owner_id and visibility, both required and with no default. The upgrade stops
if products already has rows, because it cannot invent an owner or a visibility for them.
product_elements lists the glossary elements a product holds. It is append-only, and it reuses the
0004 trigger function that refuses an element_key with no row in glossary_elements.

Task 2: product_grants holds grant and revoke events per user or role. It is append-only, and its
seq identity column (GENERATED ALWAYS) gives the events a total order.

Task 3: requirement_elements and requirement_terms hold what a requirement asks for (glossary
elements, and ticked terms that are not in the glossary). consumer_requests is the log of every
"Request this data" with its routing outcome. All three are append-only.

Check constraints are named with op.f(): the name is final. A plain name is passed through the
naming convention a second time, which is why 0002 to 0004 created ck_<table>_ck_<table>_<name>.

Value lists are copied here on purpose: a migration must not change when code changes.
PostgreSQL 14 features only.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

ROLES = ("requester", "backend_team", "data_owner", "data_steward", "checker", "glossary_owner", "operations")
ROUTING_OUTCOMES = ("reuse", "compose", "create_product", "raise_requirement")
APPEND_ONLY = ("product_elements", "product_grants", "requirement_elements", "requirement_terms", "consumer_requests")
GLOSSARY_KEYED = ("product_elements", "requirement_elements")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")
    )


def upgrade() -> None:
    # ---- task 1: products get an owner and a visibility; product_elements
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM products) THEN
                RAISE EXCEPTION 'migration 0006 adds a required owner_id and visibility to products; '
                                'products already has rows, so set those values by hand first';
            END IF;
        END $$;
        """
    )
    op.add_column("products", sa.Column("owner_id", sa.Text(), nullable=False))
    op.add_column("products", sa.Column("visibility", sa.Text(), nullable=False))
    op.create_check_constraint(op.f("ck_products_owner_present"), "products", "length(btrim(owner_id)) > 0")
    op.create_check_constraint(op.f("ck_products_visibility"), "products", "visibility IN ('open', 'restricted')")

    op.create_table(
        "product_elements",
        _id(),
        _created_at(),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("element_key", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_product_elements"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_product_elements_product_id_products"),
        sa.UniqueConstraint("product_id", "element_key", name="uq_product_elements_product_id_element_key"),
    )

    # ---- task 2: grant and revoke events. seq orders them; the database always generates it.
    op.create_table(
        "product_grants",
        _id(),
        _created_at(),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_product_grants"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_product_grants_product_id_products"),
        sa.UniqueConstraint("seq", name="uq_product_grants_seq"),
        sa.CheckConstraint("subject_type IN ('user', 'role')", name=op.f("ck_product_grants_subject_type")),
        sa.CheckConstraint("action IN ('grant', 'revoke')", name=op.f("ck_product_grants_action")),
        sa.CheckConstraint(
            f"subject_type <> 'role' OR {_in('subject', ROLES)}", name=op.f("ck_product_grants_known_role")
        ),
        sa.CheckConstraint(
            "length(btrim(subject)) > 0 AND length(btrim(decided_by)) > 0",
            name=op.f("ck_product_grants_text_present"),
        ),
    )
    op.create_index("ix_product_grants_product_id", "product_grants", ["product_id"])

    # ---- task 3: what a requirement asks for, and the log of every request
    op.create_table(
        "requirement_elements",
        _id(),
        _created_at(),
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("element_key", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_requirement_elements"),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["requirements.id"], name="fk_requirement_elements_requirement_id_requirements"
        ),
        sa.UniqueConstraint(
            "requirement_id", "element_key", name="uq_requirement_elements_requirement_id_element_key"
        ),
    )
    op.create_table(
        "requirement_terms",
        _id(),
        _created_at(),
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("term", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_requirement_terms"),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["requirements.id"], name="fk_requirement_terms_requirement_id_requirements"
        ),
        sa.UniqueConstraint("requirement_id", "term", name="uq_requirement_terms_requirement_id_term"),
        sa.CheckConstraint("length(btrim(term)) > 0", name=op.f("ck_requirement_terms_term_present")),
    )
    op.create_table(
        "consumer_requests",
        _id(),
        _created_at(),
        sa.Column("requester_id", sa.Text(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("element_keys", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("terms", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("product_codes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("requirement_id", sa.Uuid(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_consumer_requests"),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["requirements.id"], name="fk_consumer_requests_requirement_id_requirements"
        ),
        sa.CheckConstraint(_in("outcome", ROUTING_OUTCOMES), name=op.f("ck_consumer_requests_outcome")),
        sa.CheckConstraint(
            "(requirement_id IS NOT NULL) = (outcome = 'raise_requirement')",
            name=op.f("ck_consumer_requests_requirement_matches_outcome"),
        ),
        sa.CheckConstraint(
            "cardinality(element_keys) + cardinality(terms) >= 1",
            name=op.f("ck_consumer_requests_something_requested"),
        ),
        sa.CheckConstraint(
            "length(btrim(requester_id)) > 0 AND length(btrim(query)) > 0",
            name=op.f("ck_consumer_requests_text_present"),
        ),
    )
    op.create_index("ix_consumer_requests_requester_id", "consumer_requests", ["requester_id"])

    # ---- every phase 2 table that is append-only: UPDATE, DELETE and TRUNCATE refused (function from 0002)
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()"
        )
    # ---- element_key must be in the glossary (function from 0004; the column must be named element_key)
    for table in GLOSSARY_KEYED:
        op.execute(
            f"CREATE TRIGGER trg_{table}_glossary_key BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION amaze_require_glossary_key()"
        )


def downgrade() -> None:
    # Dropping a table drops its triggers and indexes; dropping a column drops the check constraints on it.
    op.drop_table("consumer_requests")
    op.drop_table("requirement_terms")
    op.drop_table("requirement_elements")
    op.drop_table("product_grants")
    op.drop_table("product_elements")
    op.drop_column("products", "visibility")
    op.drop_column("products", "owner_id")
