"""constraint names: drop the repeated prefix from 0002-0004 check constraints

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-14

0002 to 0004 passed full names (ck_products_code_format) to a metadata that has a naming
convention, so each was prefixed again: the database holds ck_products_ck_products_code_format.
This renames every such check constraint to the convention name, the name the table classes
produce. Only names change; definitions do not. The downgrade restores the old names.

From now on migrations name constraints with op.f(), which marks a name as final
(phase 2 plan, section 17). The list is written out on purpose: a migration must not change when
code changes. ALTER TABLE ... RENAME CONSTRAINT exists since PostgreSQL 9.2.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# table -> constraint names without the ck_<table>_ prefix
RENAMED = {
    "approvals": ("decided_by", "decision", "role", "step"),
    "feeds": ("code_format", "registered_at_matches_stage", "registered_has_owner", "stage"),
    "glossary_elements": (
        "data_type", "domain_format", "key_format", "proposal_unless_seed", "text_present", "version",
    ),
    "loads": ("file_ref", "status"),
    "mappings": ("source_field", "version"),
    "products": ("code_format", "domain_format", "name_present"),
    "proposals": ("autonomy", "decided_at_matches_status", "has_approver", "kind", "known_roles", "status"),
    "requirements": ("status", "text_present"),
    "rules": ("rule_key_format", "text_present", "version"),
}


def _pairs():
    for table, names in RENAMED.items():
        for name in names:
            yield table, f"ck_{table}_ck_{table}_{name}", f"ck_{table}_{name}"


def upgrade() -> None:
    for table, old, new in _pairs():
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}")


def downgrade() -> None:
    for table, old, new in _pairs():
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {new} TO {old}")
