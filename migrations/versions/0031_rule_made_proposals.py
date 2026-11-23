"""proposals.origin accepts 'rule': a proposal made by code, with no model call and no person

Revision ID: 0031
Revises: 0030

Phase 7.12, T4. Step 12 has no model, yet each drift finding comes with proposed actions. Such an action is made by rules
(code) and decided by a person: neither an agent's (a logged call) nor a person's (an author). Making it look like either
would be a false record (rule 4), so it has its own origin.

- ck_proposals_origin: 'agent', 'manual' or 'rule'.
- ck_proposals_origin_matches_source: agent has a logged call and no author; manual has an author and no call; rule has
  neither.
- ck_proposals_rule_made_kinds: a rule-made proposal is a profile_action and has a feed.

Downgrade stops, naming 0031, while a rule-made proposal exists. PostgreSQL 14 features only.
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

MATCHES_0028 = ("(origin = 'agent' AND proposal_log_id IS NOT NULL AND author IS NULL) "
                "OR (origin = 'manual' AND proposal_log_id IS NULL AND author IS NOT NULL AND length(btrim(author)) > 0)")
MATCHES_0031 = MATCHES_0028 + " OR (origin = 'rule' AND proposal_log_id IS NULL AND author IS NULL)"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_proposals_origin"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_origin"), "proposals", "origin IN ('agent', 'manual', 'rule')")
    op.drop_constraint(op.f("ck_proposals_origin_matches_source"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_origin_matches_source"), "proposals", MATCHES_0031)
    op.create_check_constraint(op.f("ck_proposals_rule_made_kinds"), "proposals",
                               "origin <> 'rule' OR (kind = 'profile_action' AND feed_id IS NOT NULL)")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM proposals WHERE origin = 'rule') THEN
                RAISE EXCEPTION 'migration 0031 cannot be downgraded: there are rule-made proposals, which 0030 cannot hold';
            END IF;
        END $$;
        """)
    op.drop_constraint(op.f("ck_proposals_rule_made_kinds"), "proposals", type_="check")
    op.drop_constraint(op.f("ck_proposals_origin_matches_source"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_origin_matches_source"), "proposals", MATCHES_0028)
    op.drop_constraint(op.f("ck_proposals_origin"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_origin"), "proposals", "origin IN ('agent', 'manual')")
