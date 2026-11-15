"""discarded calls: every model call has exactly one proposal_log row

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-15

Review of phase 3. When a model call's answer is discarded (the requirement's status changed during the
call, or writing the proposals failed), Agent.run() logs the call in its own transaction with
discarded_reason set, and creates no proposal. NULL means the answer was used.

- proposal_log.discarded_reason: text, nullable; when set, not blank. Adding a column fires no row
  trigger, so the append-only table keeps its rows untouched.
- A proposal cannot point at a discarded call (trigger on proposals).

The function body is written out here on purpose. PostgreSQL 14 features only.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

FUNCTION = """
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
    op.add_column("proposal_log", sa.Column("discarded_reason", sa.Text(), nullable=True))
    op.create_check_constraint(op.f("ck_proposal_log_discarded_reason"), "proposal_log",
                               "discarded_reason IS NULL OR length(btrim(discarded_reason)) > 0")
    op.execute(FUNCTION)
    op.execute("CREATE TRIGGER trg_proposals_not_of_a_discarded_call BEFORE INSERT ON proposals "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_proposal_of_discarded_call()")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM proposal_log WHERE discarded_reason IS NOT NULL) THEN
                RAISE EXCEPTION 'migration 0011 cannot be downgraded: a discarded call is logged, and 0010 '
                                'cannot tell it from a used one';
            END IF;
        END $$;
        """
    )
    op.execute("DROP TRIGGER trg_proposals_not_of_a_discarded_call ON proposals")
    op.execute("DROP FUNCTION amaze_refuse_proposal_of_discarded_call()")
    op.drop_constraint(op.f("ck_proposal_log_discarded_reason"), "proposal_log", type_="check")
    op.drop_column("proposal_log", "discarded_reason")
