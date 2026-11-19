"""proposals.kind accepts extracted_table

Revision ID: 0022
Revises: 0021

Phase 7.8, stage S2 (task 2.3; decision D4). The TableExtractorAgent reads the table of a scanned PDF from its page
images and proposes it; the operator confirms it before a feed is made from it. The proposal has no feed or
requirement (the feed is created at the confirmation), so no feed guard from 0016, 0017 or 0018 applies to it.

- proposals.kind: the CHECK gains 'extracted_table'. The kind list is copied here on purpose: a migration must not
  change when code changes.

Downgrade stops, naming 0022, while an extracted_table proposal exists (0021's list would refuse it), instead of
failing half-way. PostgreSQL 14 features only.
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

KINDS_0021 = ("scope", "source_list", "profile_summary", "glossary_element", "mapping", "rule", "review_summary")
KINDS_0022 = KINDS_0021 + ("extracted_table",)


def _in(values: tuple[str, ...]) -> str:
    return f"kind IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_proposals_kind"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_kind"), "proposals", _in(KINDS_0022))


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM proposals WHERE kind = 'extracted_table') THEN
                RAISE EXCEPTION 'migration 0022 cannot be downgraded: there are extracted_table proposals, which '
                                '0021 cannot hold';
            END IF;
        END $$;
        """
    )
    op.drop_constraint(op.f("ck_proposals_kind"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_kind"), "proposals", _in(KINDS_0021))
