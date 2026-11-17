"""guards after the independent review of phase 5

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-17

Phase 5 plan, section 17, "Fixes after the independent review".
- glossary_elements (finding 1): amaze_require_glossary_option() refuses an alias row whose alias the last
  version already has. The row is otherwise exactly the option (last version plus the alias), so 0016 let
  it through with the alias twice.

- proposals (finding 4): ck_proposals_names_a_column is replaced. A glossary_element or mapping proposal has
  a feed, and its payload's source_field is a JSON string that is not blank. With no feed, or with
  {"source_field": null}, uq_proposals_one_pending_per_feed_column never conflicted (NULLs are distinct).
- glossary_runs (finding 4): amaze_guard_glossary_run() checks a new run: its profile is a profile of its
  feed, and the feed is registered.

Every check on a nullable value spells NULL out (feed_id IS NOT NULL; COALESCE on jsonb_typeof of a key that
may be missing). Function bodies are written out here on purpose, the 0016 ones too (for the downgrade).
The downgrade restores the 0016 check, which every row that passes the 0017 one also passes. PostgreSQL 14
or older only: = ANY(array), jsonb operators and jsonb_typeof, plpgsql.
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


# jsonb_typeof of a missing key is NULL, and NULL = 'string' would let the row through: COALESCE
NAMES_A_COLUMN_0017 = ("kind NOT IN ('glossary_element', 'mapping') "
                       "OR (feed_id IS NOT NULL "
                       "AND COALESCE(jsonb_typeof(payload -> 'source_field') = 'string', false) "
                       "AND length(btrim(payload ->> 'source_field')) > 0)")
NAMES_A_COLUMN_0016 = ("kind NOT IN ('glossary_element', 'mapping') "
                       "OR (jsonb_typeof(payload) = 'object' AND payload ? 'source_field')")

_RUN_HEAD = """
CREATE OR REPLACE FUNCTION amaze_guard_glossary_run() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    fixed text[] := ARRAY['status', 'attempt', 'lease_until', 'started_at', 'finished_at', 'proposal_log_id',
                          'results', 'error'];
    f_stage text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'glossary_runs: DELETE refused; a run is history' USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'queued' OR NEW.attempt IS DISTINCT FROM 0 THEN
            RAISE EXCEPTION 'glossary_runs: a new run is queued at attempt 0, not % at %', NEW.status, NEW.attempt
                USING ERRCODE = 'check_violation';
        END IF;
"""

# 0017: the run's profile is one of its feed's, and the feed is registered (a missing feed has no stage)
_RUN_FEED = """
        IF NOT EXISTS (SELECT 1 FROM feed_profiles WHERE id = NEW.profile_id AND feed_id = NEW.feed_id) THEN
            RAISE EXCEPTION 'glossary_runs: profile % is not a profile of feed %', NEW.profile_id, NEW.feed_id
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
        IF f_stage IS DISTINCT FROM 'registered' THEN
            RAISE EXCEPTION 'glossary_runs: feed % is %, not registered', NEW.feed_id, COALESCE(f_stage, 'missing')
                USING ERRCODE = 'check_violation';
        END IF;
"""

_RUN_TAIL = """
        RETURN NEW;
    END IF;
    IF OLD.status = 'done' OR OLD.status = 'failed' THEN
        RAISE EXCEPTION 'glossary_runs: run % is %, and a finished run never changes', OLD.id, OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (to_jsonb(NEW) - fixed - 'columns') IS DISTINCT FROM (to_jsonb(OLD) - fixed - 'columns') THEN
        RAISE EXCEPTION 'glossary_runs: the feed, profile, reason and requester of run % are fixed', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.status = 'queued' AND NEW.status = 'queued'
       AND (to_jsonb(NEW) - 'columns') IS NOT DISTINCT FROM (to_jsonb(OLD) - 'columns')
       AND NEW.columns @> OLD.columns THEN
        RETURN NEW;  -- another column sent back while the run waits
    END IF;
    IF NEW.columns IS DISTINCT FROM OLD.columns THEN
        RAISE EXCEPTION 'glossary_runs: the columns of run % only grow while it is queued', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (OLD.status = 'queued' AND NEW.status = 'running' AND NEW.attempt = OLD.attempt + 1)
       OR (OLD.status = 'running' AND NEW.status = 'running' AND NEW.attempt > OLD.attempt)
       OR (OLD.status = 'running' AND (NEW.status = 'done' OR NEW.status = 'failed') AND NEW.attempt = OLD.attempt) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'glossary_runs: run % cannot move from % (attempt %) to % (attempt %)', OLD.id, OLD.status,
        OLD.attempt, NEW.status, NEW.attempt USING ERRCODE = 'check_violation';
END $$;
"""

RUN_STATUS_0017 = _RUN_HEAD.rstrip("\n") + _RUN_FEED.rstrip("\n") + _RUN_TAIL
# The 0016 body, restored by downgrade (its unused f_stage variable is harmless).
RUN_STATUS_0016 = _RUN_HEAD.rstrip("\n") + _RUN_TAIL


def _names_a_column(condition: str) -> None:
    op.drop_constraint(op.f("ck_proposals_names_a_column"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_names_a_column"), "proposals", condition)


def upgrade() -> None:
    op.execute(GLOSSARY_OPTION_0017)
    _names_a_column(NAMES_A_COLUMN_0017)
    op.execute(RUN_STATUS_0017)


def downgrade() -> None:
    op.execute(RUN_STATUS_0016)
    _names_a_column(NAMES_A_COLUMN_0016)
    op.execute(GLOSSARY_OPTION_0016)
