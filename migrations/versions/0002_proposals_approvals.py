"""proposals, approvals, proposal_audit view, append-only trigger; decisions leave proposal_log

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13

Decisions now live only in approvals (one row per named person's decision). proposal_log keeps the
record of each model call and loses its decision columns. The upgrade stops if any proposal_log row
already holds a decision, because that decision would be lost.

The kind and role lists are copied here on purpose: a migration must not change when code changes.
PostgreSQL 14 features only.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

KINDS = ("scope", "source_list", "profile_summary", "glossary_element", "mapping", "rule", "review_summary")
ROLES = ("requester", "backend_team", "data_owner", "data_steward", "checker", "glossary_owner", "operations")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM proposal_log WHERE status <> 'pending' OR decided_by IS NOT NULL) THEN
                RAISE EXCEPTION 'migration 0002 would lose decisions recorded in proposal_log; '
                                'move them to approvals first';
            END IF;
        END $$;
        """
    )
    op.drop_index("ix_proposal_log_status", table_name="proposal_log")
    op.drop_constraint("ck_proposal_log_decision_complete", "proposal_log", type_="check")
    op.drop_constraint("ck_proposal_log_status", "proposal_log", type_="check")
    for column in ("status", "decided_by", "decided_at", "decision_note"):
        op.drop_column("proposal_log", column)

    op.create_table(
        "proposals",
        _id(),
        _created_at(),
        sa.Column("proposal_log_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("autonomy", sa.Text(), nullable=False),
        sa.Column("required_approver_roles", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requirement_id", sa.Uuid(), nullable=True),  # foreign key added in 0004
        sa.Column("feed_id", sa.Uuid(), nullable=True),  # foreign key added in 0004
        sa.PrimaryKeyConstraint("id", name="pk_proposals"),
        sa.ForeignKeyConstraint(["proposal_log_id"], ["proposal_log.id"], name="fk_proposals_proposal_log_id_proposal_log"),
        sa.CheckConstraint(_in("kind", KINDS), name="ck_proposals_kind"),
        sa.CheckConstraint("autonomy IN ('A1', 'A2')", name="ck_proposals_autonomy"),
        sa.CheckConstraint("cardinality(required_approver_roles) >= 1", name="ck_proposals_has_approver"),
        sa.CheckConstraint(
            f"required_approver_roles <@ ARRAY[{', '.join(repr(r) for r in ROLES)}]::text[]",
            name="ck_proposals_known_roles",
        ),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_proposals_status"),
        sa.CheckConstraint("(status = 'pending') = (decided_at IS NULL)", name="ck_proposals_decided_at_matches_status"),
    )
    op.create_index("ix_proposals_status", "proposals", ["status"])

    op.create_table(
        "approvals",
        _id(),
        _created_at(),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_approvals"),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], name="fk_approvals_proposal_id_proposals"),
        sa.UniqueConstraint("proposal_id", "step", name="uq_approvals_proposal_id_step"),
        sa.UniqueConstraint("proposal_id", "decided_by", name="uq_approvals_proposal_id_decided_by"),
        sa.CheckConstraint("step >= 1", name="ck_approvals_step"),
        sa.CheckConstraint(_in("role", ROLES), name="ck_approvals_role"),
        sa.CheckConstraint("length(btrim(decided_by)) > 0", name="ck_approvals_decided_by"),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_approvals_decision"),
    )

    # Append-only: one function, attached to each protected table by a trigger.
    op.execute(
        """
        CREATE FUNCTION amaze_refuse_update_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: % refused', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation',
                      HINT = 'Configuration is versioned: insert a new version instead.';
        END $$;
        """
    )
    for table in ("proposal_log", "approvals"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()"
        )

    op.execute(
        """
        CREATE VIEW proposal_audit AS
        SELECT p.id AS proposal_id, p.kind, p.status, p.payload, p.autonomy,
               p.required_approver_roles, p.created_at AS proposed_at, p.decided_at,
               l.id AS proposal_log_id, l.agent_name, l.provider, l.model, l.model_version,
               l.prompt_id, l.prompt_version, l.input_ref, l.output, l.raw_ref, l.latency_ms,
               COALESCE(
                   (SELECT jsonb_agg(jsonb_build_object(
                               'step', a.step, 'role', a.role, 'decided_by', a.decided_by,
                               'decision', a.decision, 'note', a.note, 'decided_at', a.created_at)
                           ORDER BY a.step)
                      FROM approvals a WHERE a.proposal_id = p.id),
                   '[]'::jsonb) AS decisions
          FROM proposals p
          JOIN proposal_log l ON l.id = p.proposal_log_id
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW proposal_audit")
    for table in ("approvals", "proposal_log"):
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION amaze_refuse_update_delete()")
    op.drop_table("approvals")
    op.drop_index("ix_proposals_status", table_name="proposals")
    op.drop_table("proposals")

    op.add_column("proposal_log", sa.Column("status", sa.Text(), nullable=False, server_default="pending"))
    op.add_column("proposal_log", sa.Column("decided_by", sa.Text(), nullable=True))
    op.add_column("proposal_log", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("proposal_log", sa.Column("decision_note", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_proposal_log_status", "proposal_log", "status IN ('pending', 'approved', 'rejected')"
    )
    op.create_check_constraint(
        "ck_proposal_log_decision_complete",
        "proposal_log",
        "(status = 'pending' AND decided_by IS NULL AND decided_at IS NULL)"
        " OR (status IN ('approved', 'rejected') AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
    )
    op.create_index("ix_proposal_log_status", "proposal_log", ["status"])
