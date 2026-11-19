"""the four-eyes setting: amaze_settings, amaze_four_eyes(), and the same-approver guard made to read it

Revision ID: 0020
Revises: 0019

Phase 7.8, decision D2 (task 5.1). REQUIRE_FOUR_EYES is an append-only, versioned table; the latest row wins and
NO ROW MEANS ON (fail safe: a schema without a setting behaves exactly as it did before this migration). It is a
table, not a session setting, because any connection can set a session setting silently and it leaves no history.

The guard "the same person cannot decide two steps of one proposal":
- before 0020: UNIQUE (proposal_id, decided_by) on approvals, always.
- after 0020, setting on (the default, and with no row): the same rule, as a BEFORE INSERT trigger on approvals
  that locks the proposal row and raises unique_violation (23505) naming the person and the proposal when that
  person already has an approvals row for the proposal. The refusal is the same as before.
- after 0020, setting off: the trigger lets the second row through, so one operator who holds both roles may
  decide every step. Nothing else changes: the roles still decide in order, each step is its own approvals row
  (the audit shows every role), UNIQUE (proposal_id, step) stays, and every guard that reads an approved
  proposal (0005, 0016, 0018) is untouched. The guard is replaced, never removed.

Downgrade restores the constraint. It stops, naming 0020, when one person has decided two steps of a proposal
(only possible while the setting was off), instead of failing half-way; the settings history is dropped with the
table.

PostgreSQL 14 features only: plpgsql, a STABLE function, row and statement triggers.
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

FOUR_EYES = """
CREATE FUNCTION amaze_four_eyes() RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT COALESCE((SELECT require_four_eyes FROM amaze_settings ORDER BY version DESC LIMIT 1), true)
$$
"""

APPROVALS_GUARD = """
CREATE FUNCTION amaze_approvals_four_eyes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF amaze_four_eyes() THEN
        -- two inserts for one proposal wait for each other here, so the second sees the first
        PERFORM 1 FROM proposals WHERE id = NEW.proposal_id FOR UPDATE;
        IF EXISTS (SELECT 1 FROM approvals WHERE proposal_id = NEW.proposal_id AND decided_by = NEW.decided_by) THEN
            RAISE EXCEPTION '% already decided a step of proposal %', NEW.decided_by, NEW.proposal_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'REQUIRE_FOUR_EYES is on: a second person must decide the next step.';
        END IF;
    END IF;
    RETURN NEW;
END $$
"""


def upgrade() -> None:
    op.create_table(
        "amaze_settings",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("require_four_eyes", sa.Boolean(), nullable=False),
        sa.Column("set_by", sa.Text(), nullable=False),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_amaze_settings")),
        sa.UniqueConstraint("version", name=op.f("uq_amaze_settings_version")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_amaze_settings_version")),
        sa.CheckConstraint("length(btrim(set_by)) > 0", name=op.f("ck_amaze_settings_set_by")),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_amaze_settings_reason")),
    )
    # Append-only, with the function from 0002 (UPDATE and DELETE per row, TRUNCATE per statement).
    op.execute("CREATE TRIGGER trg_amaze_settings_append_only BEFORE UPDATE OR DELETE ON amaze_settings "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_amaze_settings_no_truncate BEFORE TRUNCATE ON amaze_settings "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute(FOUR_EYES)

    # The guard is replaced, not removed: the constraint goes, the trigger takes its place in the same migration.
    op.drop_constraint("uq_approvals_proposal_id_decided_by", "approvals", type_="unique")
    op.execute(APPROVALS_GUARD)
    op.execute("CREATE TRIGGER trg_approvals_four_eyes BEFORE INSERT ON approvals "
               "FOR EACH ROW EXECUTE FUNCTION amaze_approvals_four_eyes()")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM approvals GROUP BY proposal_id, decided_by HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'migration 0020 cannot be downgraded: one person decided two steps of a proposal '
                                '(REQUIRE_FOUR_EYES was off); 0019 forbids it';
            END IF;
        END $$;
        """
    )
    op.execute("DROP TRIGGER trg_approvals_four_eyes ON approvals")
    op.execute("DROP FUNCTION amaze_approvals_four_eyes()")
    op.create_unique_constraint("uq_approvals_proposal_id_decided_by", "approvals", ["proposal_id", "decided_by"])
    op.execute("DROP FUNCTION amaze_four_eyes()")
    op.execute("DROP TRIGGER trg_amaze_settings_no_truncate ON amaze_settings")
    op.execute("DROP TRIGGER trg_amaze_settings_append_only ON amaze_settings")
    op.drop_table("amaze_settings")
