"""discard rows: a discarded model call is a second append-only row pointing at its call row

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-15

Review of phase 3, round 3. Agent.run() now commits each call's proposal_log row in its own short
transaction right after the call, so the row cannot be marked discarded afterwards (proposal_log is
append-only). A discard is therefore a row of its own:

- proposal_log_discards (new): proposal_log_id (unique: a call is discarded once) and a reason.
  Append-only. A call that has a proposal cannot be discarded; a proposal cannot point at a discarded
  call (both checked by triggers).
- Discards recorded by 0011 (proposal_log.discarded_reason) are moved into it; then the column, its check
  and 0011's trigger and function are dropped. Dropping a column is not an UPDATE: the append-only
  trigger does not fire.

Function bodies are written out here on purpose. PostgreSQL 14 features only.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

NO_PROPOSAL_OF_A_DISCARD = """
CREATE FUNCTION amaze_refuse_proposal_of_a_discard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM proposal_log_discards WHERE proposal_log_id = NEW.proposal_log_id) THEN
        RAISE EXCEPTION 'proposal_log row % was discarded: it can have no proposal', NEW.proposal_log_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

NO_DISCARD_OF_A_PROPOSING_CALL = """
CREATE FUNCTION amaze_refuse_discard_of_a_proposing_call() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM proposals WHERE proposal_log_id = NEW.proposal_log_id) THEN
        RAISE EXCEPTION 'proposal_log row % has a proposal: it cannot be discarded', NEW.proposal_log_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

# 0011's function, restored by downgrade.
DISCARDED_CALL_0011 = """
CREATE FUNCTION amaze_refuse_proposal_of_discarded_call() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM proposal_log WHERE id = NEW.proposal_log_id AND discarded_reason IS NOT NULL) THEN
        RAISE EXCEPTION 'proposal_log row % was discarded: it can have no proposal', NEW.proposal_log_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "proposal_log_discards",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.Column("proposal_log_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_log_discards")),
        sa.ForeignKeyConstraint(["proposal_log_id"], ["proposal_log.id"],
                                name=op.f("fk_proposal_log_discards_proposal_log_id_proposal_log")),
        sa.UniqueConstraint("proposal_log_id", name=op.f("uq_proposal_log_discards_proposal_log_id")),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_proposal_log_discards_reason")),
    )
    op.execute("INSERT INTO proposal_log_discards (proposal_log_id, reason) "
               "SELECT id, discarded_reason FROM proposal_log WHERE discarded_reason IS NOT NULL")
    op.execute("CREATE TRIGGER trg_proposal_log_discards_append_only BEFORE UPDATE OR DELETE ON proposal_log_discards "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_proposal_log_discards_no_truncate BEFORE TRUNCATE ON proposal_log_discards "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute(NO_DISCARD_OF_A_PROPOSING_CALL)
    op.execute("CREATE TRIGGER trg_proposal_log_discards_no_proposal BEFORE INSERT ON proposal_log_discards "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_discard_of_a_proposing_call()")

    op.execute("DROP TRIGGER trg_proposals_not_of_a_discarded_call ON proposals")
    op.execute("DROP FUNCTION amaze_refuse_proposal_of_discarded_call()")
    op.execute(NO_PROPOSAL_OF_A_DISCARD)
    op.execute("CREATE TRIGGER trg_proposals_not_of_a_discard BEFORE INSERT ON proposals "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_proposal_of_a_discard()")

    op.drop_constraint(op.f("ck_proposal_log_discarded_reason"), "proposal_log", type_="check")
    op.drop_column("proposal_log", "discarded_reason")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM proposal_log_discards) THEN
                RAISE EXCEPTION 'migration 0012 cannot be downgraded: a discard row exists, and 0011 cannot '
                                'hold it without updating an append-only proposal_log row';
            END IF;
        END $$;
        """
    )
    op.add_column("proposal_log", sa.Column("discarded_reason", sa.Text(), nullable=True))
    op.create_check_constraint(op.f("ck_proposal_log_discarded_reason"), "proposal_log",
                               "discarded_reason IS NULL OR length(btrim(discarded_reason)) > 0")
    op.execute("DROP TRIGGER trg_proposals_not_of_a_discard ON proposals")
    op.execute("DROP FUNCTION amaze_refuse_proposal_of_a_discard()")
    op.execute(DISCARDED_CALL_0011)
    op.execute("CREATE TRIGGER trg_proposals_not_of_a_discarded_call BEFORE INSERT ON proposals "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_proposal_of_discarded_call()")
    op.drop_table("proposal_log_discards")  # its triggers go with it
    op.execute("DROP FUNCTION amaze_refuse_discard_of_a_proposing_call()")
