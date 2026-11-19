"""glossary_elements.unit: the unit of a measure ("times", "percent"); the element guard compares it

Revision ID: 0025
Revises: 0024

Phase 7.8, stage S6 (task 6.1; plan assumptions 40 to 42).

- glossary_elements.unit text NULL, non-blank when set (ck_glossary_elements_unit). Existing rows keep NULL.
- amaze_require_glossary_option() (0016, 0017) also compares the unit: a new element equals its option's element in
  the unit too (a missing `unit` key in an older option reads as NULL), and an alias row keeps the unit of the key's
  last version. Every other branch and message is the 0017 body, unchanged, because tests name them.

Downgrade stops, naming 0025, while any element has a unit (dropping the column would lose it), then restores the 0017
function body. The function bodies are written out here on purpose: a migration must not change when code changes.
PostgreSQL 14 features only.
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

# The 0017 body (0016's, plus "the alias is new to the element"), restored by downgrade.
GLOSSARY_OPTION_0017 = """
CREATE OR REPLACE FUNCTION amaze_require_glossary_option() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_payload jsonb;
    latest glossary_elements%ROWTYPE;
    opt jsonb;
    el jsonb;
BEGIN
    IF NEW.created_by = 'seed' THEN
        -- the seed writes version 1 of a key that does not exist yet, with no proposal, and nothing else
        IF NEW.proposal_id IS NOT NULL OR NEW.version IS DISTINCT FROM 1
           OR EXISTS (SELECT 1 FROM glossary_elements WHERE key = NEW.key) THEN
            RAISE EXCEPTION 'glossary element % version %: a seed row is version 1 of a new key, with no proposal',
                NEW.key, NEW.version USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    SELECT payload INTO p_payload FROM proposals WHERE id = NEW.proposal_id;
    IF NOT EXISTS (SELECT 1 FROM approvals WHERE proposal_id = NEW.proposal_id AND role = 'glossary_owner'
                      AND decision = 'approved' AND decided_by = NEW.created_by) THEN
        RAISE EXCEPTION 'glossary element %: created_by % is not the glossary_owner who approved proposal %',
            NEW.key, NEW.created_by, NEW.proposal_id USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO latest FROM glossary_elements WHERE key = NEW.key ORDER BY version DESC LIMIT 1;
    FOR opt IN SELECT value FROM jsonb_array_elements(COALESCE(p_payload -> 'options', '[]'::jsonb)) LOOP
        IF opt ->> 'kind' = 'new_element' AND (opt ->> 'key') IS NOT DISTINCT FROM NEW.key THEN
            el := opt -> 'element';
            IF NEW.version = 1 AND latest.id IS NULL
               AND (el ->> 'key') IS NOT DISTINCT FROM NEW.key
               AND (el ->> 'name') IS NOT DISTINCT FROM NEW.name
               AND (el ->> 'domain') IS NOT DISTINCT FROM NEW.domain
               AND (el ->> 'data_type') IS NOT DISTINCT FROM NEW.data_type
               AND (el ->> 'definition') IS NOT DISTINCT FROM NEW.definition
               AND COALESCE(el -> 'aliases', '[]'::jsonb) IS NOT DISTINCT FROM to_jsonb(NEW.aliases) THEN
                RETURN NEW;
            END IF;
        ELSIF opt ->> 'kind' = 'alias' AND (opt ->> 'key') IS NOT DISTINCT FROM NEW.key
              AND (opt ->> 'alias') IS NOT NULL AND latest.id IS NOT NULL THEN
            IF NEW.version = latest.version + 1
               AND NEW.name IS NOT DISTINCT FROM latest.name
               AND NEW.domain IS NOT DISTINCT FROM latest.domain
               AND NEW.data_type IS NOT DISTINCT FROM latest.data_type
               AND NEW.definition IS NOT DISTINCT FROM latest.definition
               AND NEW.aliases IS NOT DISTINCT FROM (latest.aliases || ARRAY[opt ->> 'alias']) THEN
                IF (opt ->> 'alias') = ANY(latest.aliases) THEN
                    RAISE EXCEPTION 'glossary element %: version % already has the alias %', NEW.key, latest.version,
                        opt ->> 'alias' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
        END IF;
    END LOOP;
    RAISE EXCEPTION 'glossary element % version % is none of the options of proposal %', NEW.key, NEW.version,
        NEW.proposal_id USING ERRCODE = 'check_violation';
END $$;
"""

# 0025: the 0017 body with the unit compared: the new element's unit equals its option's (a missing key is NULL),
# and an alias row's unit equals the last version's.
GLOSSARY_OPTION_0025 = GLOSSARY_OPTION_0017.replace(
    "               AND COALESCE(el -> 'aliases', '[]'::jsonb) IS NOT DISTINCT FROM to_jsonb(NEW.aliases) THEN",
    "               AND COALESCE(el -> 'aliases', '[]'::jsonb) IS NOT DISTINCT FROM to_jsonb(NEW.aliases)\n"
    "               AND (el ->> 'unit') IS NOT DISTINCT FROM NEW.unit THEN",
).replace(
    "               AND NEW.definition IS NOT DISTINCT FROM latest.definition\n"
    "               AND NEW.aliases IS NOT DISTINCT FROM (latest.aliases || ARRAY[opt ->> 'alias']) THEN",
    "               AND NEW.definition IS NOT DISTINCT FROM latest.definition\n"
    "               AND NEW.unit IS NOT DISTINCT FROM latest.unit\n"
    "               AND NEW.aliases IS NOT DISTINCT FROM (latest.aliases || ARRAY[opt ->> 'alias']) THEN",
)


def upgrade() -> None:
    assert GLOSSARY_OPTION_0025.count("NEW.unit") == 2 and GLOSSARY_OPTION_0025 != GLOSSARY_OPTION_0017
    op.add_column("glossary_elements", sa.Column("unit", sa.Text(), nullable=True))
    op.create_check_constraint(op.f("ck_glossary_elements_unit"), "glossary_elements",
                               "unit IS NULL OR length(btrim(unit)) > 0")
    op.execute(GLOSSARY_OPTION_0025)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM glossary_elements WHERE unit IS NOT NULL) THEN
                RAISE EXCEPTION 'migration 0025 cannot be downgraded: a glossary element has a unit, which 0024 cannot hold';
            END IF;
        END $$;
        """
    )
    op.execute(GLOSSARY_OPTION_0017)
    op.drop_constraint(op.f("ck_glossary_elements_unit"), "glossary_elements", type_="check")
    op.drop_column("glossary_elements", "unit")
