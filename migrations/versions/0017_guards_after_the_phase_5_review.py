"""guards after the independent review of phase 5

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-17

Phase 5 plan, section 17, "Fixes after the independent review".
- glossary_elements (finding 1): amaze_require_glossary_option() refuses an alias row whose alias the last
  version already has. The row is otherwise exactly the option (last version plus the alias), so 0016 let
  it through with the alias twice.

Function bodies are written out here on purpose, the 0016 ones too (for the downgrade). PostgreSQL 14 or
older only: = ANY(array), jsonb operators, plpgsql.
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_GLOSSARY_OPTION_HEAD = """
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
"""

_GLOSSARY_OPTION_TAIL = """
                RETURN NEW;
            END IF;
        END IF;
    END LOOP;
    RAISE EXCEPTION 'glossary element % version % is none of the options of proposal %', NEW.key, NEW.version,
        NEW.proposal_id USING ERRCODE = 'check_violation';
END $$;
"""

# 0017: the alias is new to the element (opt ->> 'alias' is not NULL here, so = ANY is true or false)
_ALIAS_IS_NEW = """
                IF (opt ->> 'alias') = ANY(latest.aliases) THEN
                    RAISE EXCEPTION 'glossary element %: version % already has the alias %', NEW.key, latest.version,
                        opt ->> 'alias' USING ERRCODE = 'check_violation';
                END IF;
"""

GLOSSARY_OPTION_0017 = _GLOSSARY_OPTION_HEAD.rstrip("\n") + _ALIAS_IS_NEW.rstrip("\n") + _GLOSSARY_OPTION_TAIL
# The 0016 body, restored by downgrade.
GLOSSARY_OPTION_0016 = _GLOSSARY_OPTION_HEAD.rstrip("\n") + _GLOSSARY_OPTION_TAIL


def upgrade() -> None:
    op.execute(GLOSSARY_OPTION_0017)


def downgrade() -> None:
    op.execute(GLOSSARY_OPTION_0016)
