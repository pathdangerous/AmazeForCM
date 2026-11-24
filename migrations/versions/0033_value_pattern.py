"""amaze_value_pattern(): the shape of the values a rule looks at, the one definition of an exception group's pattern

Revision ID: 0033
Revises: 0032

Phase 7.14, T1 (plan: docs/plans/phase-7.14.md, section 2.1). An exception group is a rule key and a value pattern
within one load. The pattern of a standard record for a list of element keys: for each key in "C" collation order,
the value as text (jsonb ->>) with digit runs replaced by 9, upper-case ASCII runs by A and lower-case ASCII runs by a,
other characters kept; a missing value, a JSON null or a value of spaces only is <empty>; joined with ' | '. No key:
(row). Immutable, so the view counts groups in SQL and 0034's waiver guard can compare a record with a waiver.
core/engine/pattern.py is its Python twin (tests/test_exceptions_view.py checks they agree).

Nothing else changes. Downgrade drops the function (nothing stored depends on it in 0033). PostgreSQL 14 features only.
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

VALUE_PATTERN = r"""
CREATE FUNCTION amaze_value_pattern(v jsonb, keys text[]) RETURNS text LANGUAGE sql IMMUTABLE AS $$
SELECT CASE WHEN coalesce(cardinality(keys), 0) = 0 THEN '(row)' ELSE (
    SELECT string_agg(
        CASE WHEN v ->> k IS NULL OR btrim(v ->> k) = '' THEN '<empty>'
             ELSE regexp_replace(regexp_replace(regexp_replace(v ->> k, '[0-9]+', '9', 'g'),
                                                '[A-Z]+', 'A', 'g'), '[a-z]+', 'a', 'g') END,
        ' | ' ORDER BY k COLLATE "C")
    FROM (SELECT DISTINCT k FROM unnest(keys) AS u(k)) AS d) END
$$;
"""


def upgrade() -> None:
    op.execute(VALUE_PATTERN)


def downgrade() -> None:
    op.execute("DROP FUNCTION amaze_value_pattern(jsonb, text[])")
