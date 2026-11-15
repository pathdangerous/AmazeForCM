"""requester approvals: only the requirement's own requester approves its proposals as requester

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-15

Review of phase 3, fix 3. 0005's function checks that every required role approved a proposal, not who
approved it. For the requester role that is not enough: a requester-role approval of a proposal that
belongs to a requirement must come from that requirement's requester (phase 3 plan, assumption 29).
The steps already refuse anyone else (NotTheRequester); this makes the database refuse it too.

Other roles and proposals with no requirement are not affected. Rows already in approvals are not
re-checked (a trigger fires on insert only). The function body is written out here on purpose: a
migration must not change when code changes. PostgreSQL 14 features only: plpgsql, a trigger with WHEN.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

FUNCTION = """
CREATE FUNCTION amaze_require_requester_of_requirement() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_requirement uuid;
    requester text;
BEGIN
    SELECT requirement_id INTO p_requirement FROM proposals WHERE id = NEW.proposal_id;
    IF p_requirement IS NULL THEN
        RETURN NEW;  -- no requirement (or no proposal: the foreign key reports that)
    END IF;
    SELECT requester_id INTO requester FROM requirements WHERE id = p_requirement;
    IF NEW.decided_by IS DISTINCT FROM requester THEN
        RAISE EXCEPTION 'only the requirement''s requester (%) decides here, not %', requester, NEW.decided_by
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.execute(FUNCTION)
    op.execute("CREATE TRIGGER trg_approvals_requester_of_requirement BEFORE INSERT ON approvals "
               "FOR EACH ROW WHEN (NEW.role = 'requester') "
               "EXECUTE FUNCTION amaze_require_requester_of_requirement()")


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_approvals_requester_of_requirement ON approvals")
    op.execute("DROP FUNCTION amaze_require_requester_of_requirement()")
