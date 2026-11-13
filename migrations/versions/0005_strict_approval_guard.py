"""configuration guard: every required role approved, no rejection

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-13

Replaces amaze_require_approved_proposal() from 0004. A row in mappings, rules or glossary_elements
(except the glossary seed) now needs a proposal that is approved, of the matching kind, has no
'rejected' row in approvals, and has an 'approved' row for every role in required_approver_roles.
0004 asked for at least one 'approved' row, so a two-step proposal forced to 'approved' after step 1
passed (phase 1 plan, section 19, finding 1).

The triggers from 0004 stay; only the function body changes. Both bodies are written out here on
purpose: a migration must not change when another file changes. PostgreSQL 14 features only.
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

STRICT = """
CREATE OR REPLACE FUNCTION amaze_require_approved_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    wanted text := TG_ARGV[0];
    p_status text;
    p_kind text;
    p_roles text[];
    missing_role text;
BEGIN
    -- Nested on purpose: PL/pgSQL does not short-circuit AND, and only glossary_elements
    -- has a created_by column.
    IF TG_TABLE_NAME = 'glossary_elements' THEN
        IF NEW.created_by = 'seed' THEN
            RETURN NEW;
        END IF;
    END IF;
    IF NEW.proposal_id IS NULL THEN
        RAISE EXCEPTION '% row needs an approved % proposal', TG_TABLE_NAME, wanted
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT status, kind, required_approver_roles INTO p_status, p_kind, p_roles
      FROM proposals WHERE id = NEW.proposal_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'proposal % does not exist', NEW.proposal_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF p_kind <> wanted THEN
        RAISE EXCEPTION 'proposal % is a % proposal, not %', NEW.proposal_id, p_kind, wanted
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_status <> 'approved' THEN
        RAISE EXCEPTION 'proposal % is %, not approved', NEW.proposal_id, p_status
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM approvals
                WHERE proposal_id = NEW.proposal_id AND decision = 'rejected') THEN
        RAISE EXCEPTION 'proposal % has a rejected decision in approvals', NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
    -- The first required role, in approval order, with no 'approved' row of its own.
    SELECT r.role INTO missing_role
      FROM unnest(p_roles) WITH ORDINALITY AS r(role, position)
     WHERE NOT EXISTS (SELECT 1 FROM approvals a
                        WHERE a.proposal_id = NEW.proposal_id
                          AND a.role = r.role AND a.decision = 'approved')
     ORDER BY r.position
     LIMIT 1;
    IF missing_role IS NOT NULL THEN
        RAISE EXCEPTION 'proposal % has no approved decision from role %', NEW.proposal_id, missing_role
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

# The 0004 body, restored by downgrade.
AT_LEAST_ONE = """
CREATE OR REPLACE FUNCTION amaze_require_approved_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    wanted text := TG_ARGV[0];
    p_status text;
    p_kind text;
BEGIN
    -- Nested on purpose: PL/pgSQL does not short-circuit AND, and only glossary_elements
    -- has a created_by column.
    IF TG_TABLE_NAME = 'glossary_elements' THEN
        IF NEW.created_by = 'seed' THEN
            RETURN NEW;
        END IF;
    END IF;
    IF NEW.proposal_id IS NULL THEN
        RAISE EXCEPTION '% row needs an approved % proposal', TG_TABLE_NAME, wanted
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT status, kind INTO p_status, p_kind FROM proposals WHERE id = NEW.proposal_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'proposal % does not exist', NEW.proposal_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF p_kind <> wanted THEN
        RAISE EXCEPTION 'proposal % is a % proposal, not %', NEW.proposal_id, p_kind, wanted
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_status <> 'approved' THEN
        RAISE EXCEPTION 'proposal % is %, not approved', NEW.proposal_id, p_status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM approvals
                    WHERE proposal_id = NEW.proposal_id AND decision = 'approved') THEN
        RAISE EXCEPTION 'proposal % has no approved decision in approvals', NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.execute(STRICT)


def downgrade() -> None:
    op.execute(AT_LEAST_ONE)
