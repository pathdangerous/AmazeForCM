"""transformations and key policies: the transformation proposal kind, two tables, go-live pins them

Revision ID: 0023
Revises: 0022

Phase 7.8, stage S4 (task 4.1; plan assumptions 27 to 29).
- proposals.kind: the CHECK gains 'transformation'. A transformation proposal has a feed and names its column
  (ck_proposals_names_a_transformation); one pending per feed and column
  (uq_proposals_one_pending_transformation_per_feed_column).
- amaze_valid_transform_steps(jsonb): the fixed safe set, written out once for the CHECK. A non-empty array of 1 to 6
  objects; trim {op}; placeholder_to_null {op, values: 1..10 strings of at most 16 characters}; parse_date {op,
  format: one of seven}; cast {op, type: one of six}; rename {op, to: snake_case, at most 63 characters}; each op at
  most once, rename last. No other key, no other op.
- transformations (new, append-only): a row needs its approved transformation proposal (the 0005 guard, with every
  required role) and matches it: same feed, same column, same steps, the column's next version, a registered feed,
  a column that has a mapping on the feed (trg_transformations_b_matches_proposal). Mappings are never removed, so
  "mapped" is "has a mapping row".
- feed_key_policies (new, append-only): what a load does with duplicated keys, versioned per feed; the feed is
  registered. Who may set it is a role check of the step, not of the database.
- feed_go_lives.transformation_ids: the go-live guard (0018, written out in full here) also requires the pinned ids
  to be those of its summary and exactly the feed's current transformations (the latest version of each mapped
  column). The default '{}' keeps a feed with no transformations going live as before.

Function bodies are written out here on purpose: a migration must not change when code changes. Every check on a
nullable value spells NULL out. PostgreSQL 14 features only: plpgsql, jsonb, jsonb_array_length, partial unique
indexes on an expression, uuid[].

Downgrade stops, naming 0023, while a transformation proposal, a transformation or a key policy exists.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

KINDS_0022 = ("scope", "source_list", "profile_summary", "glossary_element", "mapping", "rule", "review_summary",
              "extracted_table")
KINDS_0023 = KINDS_0022 + ("transformation",)

NAMES_A_TRANSFORMATION = ("kind <> 'transformation' "
                          "OR (feed_id IS NOT NULL "
                          "AND COALESCE(jsonb_typeof(payload -> 'source_field') = 'string', false) "
                          "AND length(btrim(payload ->> 'source_field')) > 0)")

VALID_STEPS = """
CREATE FUNCTION amaze_valid_transform_steps(steps jsonb) RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    n integer;
    step jsonb;
    kind text;
    seen text[] := ARRAY[]::text[];
    keys integer;
BEGIN
    IF steps IS NULL OR jsonb_typeof(steps) IS DISTINCT FROM 'array' THEN
        RETURN false;
    END IF;
    n := jsonb_array_length(steps);
    IF n < 1 OR n > 6 THEN
        RETURN false;
    END IF;
    FOR i IN 0 .. n - 1 LOOP
        step := steps -> i;
        IF jsonb_typeof(step) IS DISTINCT FROM 'object' OR jsonb_typeof(step -> 'op') IS DISTINCT FROM 'string' THEN
            RETURN false;
        END IF;
        kind := step ->> 'op';
        IF kind = ANY(seen) OR 'rename' = ANY(seen) THEN
            RETURN false;  -- an op twice, or a step after rename
        END IF;
        seen := seen || kind;
        SELECT count(*) INTO keys FROM jsonb_object_keys(step);
        IF kind = 'trim' THEN
            IF keys <> 1 THEN
                RETURN false;
            END IF;
        ELSIF kind = 'placeholder_to_null' THEN
            IF keys <> 2 OR NOT COALESCE(jsonb_typeof(step -> 'values') = 'array', false) THEN
                RETURN false;
            END IF;
            IF jsonb_array_length(step -> 'values') < 1 OR jsonb_array_length(step -> 'values') > 10 THEN
                RETURN false;
            END IF;
            IF EXISTS (SELECT 1 FROM jsonb_array_elements(step -> 'values') AS e(v)
                        WHERE jsonb_typeof(e.v) IS DISTINCT FROM 'string' OR char_length(e.v #>> '{}') > 16) THEN
                RETURN false;
            END IF;
        ELSIF kind = 'parse_date' THEN
            IF keys <> 2 OR NOT COALESCE(jsonb_typeof(step -> 'format') = 'string', false) THEN
                RETURN false;
            END IF;
            IF NOT ((step ->> 'format') = ANY(ARRAY['DD-MM-YYYY', 'MM-DD-YYYY', 'DD/MM/YYYY', 'MM/DD/YYYY',
                                                     'YYYY-MM-DD', 'YYYYMMDD', 'DD.MM.YYYY'])) THEN
                RETURN false;
            END IF;
        ELSIF kind = 'cast' THEN
            IF keys <> 2 OR NOT COALESCE(jsonb_typeof(step -> 'type') = 'string', false) THEN
                RETURN false;
            END IF;
            IF NOT ((step ->> 'type') = ANY(ARRAY['string', 'integer', 'decimal', 'date', 'datetime', 'boolean'])) THEN
                RETURN false;
            END IF;
        ELSIF kind = 'rename' THEN
            IF keys <> 2 OR NOT COALESCE(jsonb_typeof(step -> 'to') = 'string', false) THEN
                RETURN false;
            END IF;
            IF NOT ((step ->> 'to') ~ '^[a-z][a-z0-9_]*$') OR char_length(step ->> 'to') > 63 THEN
                RETURN false;
            END IF;
        ELSE
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END $$;
"""

VALID_KEY_COLUMNS = """
CREATE FUNCTION amaze_valid_key_columns(cols text[]) RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF cols IS NULL OR cardinality(cols) < 1 OR cardinality(cols) > 5 THEN
        RETURN false;
    END IF;
    IF EXISTS (SELECT 1 FROM unnest(cols) AS c(name) WHERE c.name IS NULL OR length(btrim(c.name)) = 0) THEN
        RETURN false;
    END IF;
    RETURN (SELECT count(DISTINCT c.name) FROM unnest(cols) AS c(name)) = cardinality(cols);
END $$;
"""

TRANSFORMATION_MATCHES = """
CREATE FUNCTION amaze_require_transformation_matches_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_feed uuid;
    p_payload jsonb;
    latest integer;
    f_stage text;
BEGIN
    SELECT feed_id, payload INTO p_feed, p_payload FROM proposals WHERE id = NEW.proposal_id;
    IF p_feed IS DISTINCT FROM NEW.feed_id THEN
        RAISE EXCEPTION 'transformation: proposal % is for feed %, not %', NEW.proposal_id,
            COALESCE(p_feed::text, 'no feed'), NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    IF (p_payload ->> 'source_field') IS DISTINCT FROM NEW.source_field THEN
        RAISE EXCEPTION 'transformation: proposal % is for column %, not %', NEW.proposal_id,
            COALESCE(p_payload ->> 'source_field', 'none'), NEW.source_field USING ERRCODE = 'check_violation';
    END IF;
    IF (p_payload -> 'steps') IS DISTINCT FROM NEW.steps THEN
        RAISE EXCEPTION 'transformation: proposal % holds other steps than the row', NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(version) INTO latest FROM transformations
     WHERE feed_id = NEW.feed_id AND source_field = NEW.source_field;
    IF NEW.version IS DISTINCT FROM COALESCE(latest, 0) + 1 THEN
        RAISE EXCEPTION 'transformation: version % of column % is not the next one (%)', NEW.version,
            NEW.source_field, COALESCE(latest, 0) + 1 USING ERRCODE = 'check_violation';
    END IF;
    SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
    IF f_stage IS DISTINCT FROM 'registered' THEN
        RAISE EXCEPTION 'transformation: feed % is %, not registered', NEW.feed_id, COALESCE(f_stage, 'missing')
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM mappings WHERE feed_id = NEW.feed_id AND source_field = NEW.source_field) THEN
        RAISE EXCEPTION 'transformation: column % of feed % is not mapped', NEW.source_field, NEW.feed_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

KEY_POLICY_GUARD = """
CREATE FUNCTION amaze_guard_feed_key_policy() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    f_stage text;
BEGIN
    SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
    IF f_stage IS DISTINCT FROM 'registered' THEN
        RAISE EXCEPTION 'key policy: feed % is %, not registered', NEW.feed_id, COALESCE(f_stage, 'missing')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

# The go-live guard, in two bodies: the 0018 one (restored by downgrade) and the 0023 one. They differ in the
# transformation_ids declarations and the block marked below.
_GO_LIVE_HEAD = """
CREATE OR REPLACE FUNCTION amaze_guard_feed_go_live() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_kind text;
    p_feed uuid;
    p_status text;
    p_payload jsonb;
    p_mappings uuid[];
    p_rules uuid[];
    p_transformations uuid[];
    current_transformations uuid[];
    r_owner text;
    l_feed uuid;
    l_status text;
    l_purpose text;
    last_version integer;
BEGIN
    SELECT kind, feed_id, status, payload INTO p_kind, p_feed, p_status, p_payload
      FROM proposals WHERE id = NEW.proposal_id;
    IF p_kind IS DISTINCT FROM 'review_summary' OR p_feed IS DISTINCT FROM NEW.feed_id
       OR p_status IS DISTINCT FROM 'approved' THEN
        RAISE EXCEPTION 'go-live: proposal % is not an approved review_summary of feed %', NEW.proposal_id,
            NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM approvals WHERE proposal_id = NEW.proposal_id AND role = 'data_owner'
                      AND decision = 'approved' AND decided_by = NEW.approved_by) THEN
        RAISE EXCEPTION 'go-live: approved_by % did not decide proposal % as data_owner', NEW.approved_by,
            NEW.proposal_id USING ERRCODE = 'check_violation';
    END IF;
    SELECT owner_id INTO r_owner FROM feed_registrations WHERE feed_id = NEW.feed_id ORDER BY version DESC LIMIT 1;
    IF r_owner IS DISTINCT FROM NEW.approved_by THEN
        RAISE EXCEPTION 'go-live: approved_by % is not the owner of feed %', NEW.approved_by, NEW.feed_id
            USING ERRCODE = 'check_violation';
    END IF;
    p_mappings := ARRAY(SELECT x::uuid FROM jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(p_payload -> 'mapping_ids') = 'array' THEN p_payload -> 'mapping_ids'
             ELSE '[]'::jsonb END) AS t(x) ORDER BY 1);
    IF p_mappings IS DISTINCT FROM ARRAY(SELECT u FROM unnest(NEW.mapping_ids) AS m(u) ORDER BY 1) THEN
        RAISE EXCEPTION 'go-live: mapping_ids are not those of proposal %', NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
    p_rules := ARRAY(SELECT x::uuid FROM jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(p_payload -> 'rule_ids') = 'array' THEN p_payload -> 'rule_ids'
             ELSE '[]'::jsonb END) AS t(x) ORDER BY 1);
    IF p_rules IS DISTINCT FROM ARRAY(SELECT u FROM unnest(NEW.rule_ids) AS m(u) ORDER BY 1) THEN
        RAISE EXCEPTION 'go-live: rule_ids are not those of proposal %', NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
"""

_GO_LIVE_TRANSFORMATIONS = """
    p_transformations := ARRAY(SELECT x::uuid FROM jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(p_payload -> 'transformation_ids') = 'array' THEN p_payload -> 'transformation_ids'
             ELSE '[]'::jsonb END) AS t(x) ORDER BY 1);
    IF p_transformations IS DISTINCT FROM ARRAY(SELECT u FROM unnest(NEW.transformation_ids) AS m(u) ORDER BY 1) THEN
        RAISE EXCEPTION 'go-live: transformation_ids are not those of proposal %', NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
    -- the latest version of each column that has a mapping on the feed
    current_transformations := ARRAY(
        SELECT t.id FROM transformations t
         WHERE t.feed_id = NEW.feed_id
           AND t.version = (SELECT max(v.version) FROM transformations v
                             WHERE v.feed_id = t.feed_id AND v.source_field = t.source_field)
           AND EXISTS (SELECT 1 FROM mappings m WHERE m.feed_id = t.feed_id AND m.source_field = t.source_field)
         ORDER BY t.id);
    IF current_transformations IS DISTINCT FROM ARRAY(SELECT u FROM unnest(NEW.transformation_ids) AS m(u) ORDER BY 1) THEN
        RAISE EXCEPTION 'go-live: transformation_ids are not the current transformations of feed %', NEW.feed_id
            USING ERRCODE = 'check_violation';
    END IF;
"""

_GO_LIVE_TAIL = """
    IF (p_payload ->> 'test_load_id') IS DISTINCT FROM NEW.test_load_id::text THEN
        RAISE EXCEPTION 'go-live: test_load_id is not the one of proposal %', NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT feed_id, status, purpose INTO l_feed, l_status, l_purpose FROM loads WHERE id = NEW.test_load_id;
    IF l_feed IS DISTINCT FROM NEW.feed_id OR l_status IS DISTINCT FROM 'loaded'
       OR l_purpose IS DISTINCT FROM 'go_live_test' THEN
        RAISE EXCEPTION 'go-live: load % is not a loaded go_live_test of feed %', NEW.test_load_id, NEW.feed_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(version) INTO last_version FROM feed_go_lives WHERE feed_id = NEW.feed_id;
    IF NEW.version IS DISTINCT FROM COALESCE(last_version, 0) + 1 THEN
        RAISE EXCEPTION 'go-live: version % is not the next one (%)', NEW.version, COALESCE(last_version, 0) + 1
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

GO_LIVE_GUARD_0023 = _GO_LIVE_HEAD + _GO_LIVE_TRANSFORMATIONS + _GO_LIVE_TAIL
# The 0018 body, restored by downgrade: no transformation variables, no transformation block.
GO_LIVE_GUARD_0018 = (_GO_LIVE_HEAD
                      .replace("    p_transformations uuid[];\n    current_transformations uuid[];\n", "")
                      + _GO_LIVE_TAIL)

NEW_TABLES = ("transformations", "feed_key_policies")


def _in(values: tuple[str, ...]) -> str:
    return f"kind IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_proposals_kind"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_kind"), "proposals", _in(KINDS_0023))
    op.create_check_constraint(op.f("ck_proposals_names_a_transformation"), "proposals", NAMES_A_TRANSFORMATION)
    op.create_index("uq_proposals_one_pending_transformation_per_feed_column", "proposals",
                    ["feed_id", sa.text("(payload ->> 'source_field')")], unique=True,
                    postgresql_where=sa.text("status = 'pending' AND kind = 'transformation'"))

    op.execute(VALID_STEPS)
    op.execute(VALID_KEY_COLUMNS)
    op.create_table(
        "transformations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("source_field", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transformations")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_transformations_feed_id_feeds")),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"],
                                name=op.f("fk_transformations_proposal_id_proposals")),
        sa.UniqueConstraint("feed_id", "source_field", "version",
                            name=op.f("uq_transformations_feed_id_source_field_version")),
        sa.CheckConstraint("length(btrim(source_field)) > 0", name=op.f("ck_transformations_source_field")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_transformations_version")),
        sa.CheckConstraint("length(btrim(created_by)) > 0", name=op.f("ck_transformations_created_by")),
        sa.CheckConstraint("amaze_valid_transform_steps(steps)", name=op.f("ck_transformations_steps")),
    )
    op.create_index("uq_transformations_proposal_id", "transformations", ["proposal_id"], unique=True)
    op.create_table(
        "feed_key_policies",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("policy", sa.Text(), nullable=False),
        sa.Column("key_columns", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("set_by", sa.Text(), nullable=False),
        sa.Column("set_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_key_policies")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_feed_key_policies_feed_id_feeds")),
        sa.UniqueConstraint("feed_id", "version", name=op.f("uq_feed_key_policies_feed_id_version")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_feed_key_policies_version")),
        sa.CheckConstraint("policy IN ('hold', 'keep_last', 'flag')", name=op.f("ck_feed_key_policies_policy")),
        sa.CheckConstraint("amaze_valid_key_columns(key_columns)", name=op.f("ck_feed_key_policies_key_columns")),
        sa.CheckConstraint("length(btrim(set_by)) > 0", name=op.f("ck_feed_key_policies_set_by")),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_feed_key_policies_reason")),
    )

    op.execute(TRANSFORMATION_MATCHES)
    op.execute(KEY_POLICY_GUARD)
    for table in NEW_TABLES:
        op.execute(f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
        op.execute(f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()")
    # name order: trg_..._approved_proposal runs before trg_..._b_matches_proposal
    op.execute("CREATE TRIGGER trg_transformations_approved_proposal BEFORE INSERT ON transformations "
               "FOR EACH ROW EXECUTE FUNCTION amaze_require_approved_proposal('transformation')")
    op.execute("CREATE TRIGGER trg_transformations_b_matches_proposal BEFORE INSERT ON transformations "
               "FOR EACH ROW EXECUTE FUNCTION amaze_require_transformation_matches_proposal()")
    op.execute("CREATE TRIGGER trg_feed_key_policies_b_guard BEFORE INSERT ON feed_key_policies "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_feed_key_policy()")

    op.add_column("feed_go_lives", sa.Column("transformation_ids", postgresql.ARRAY(sa.Uuid()),
                                             server_default=sa.text("'{}'::uuid[]"), nullable=False))
    op.execute(GO_LIVE_GUARD_0023)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM transformations) OR EXISTS (SELECT 1 FROM feed_key_policies)
               OR EXISTS (SELECT 1 FROM proposals WHERE kind = 'transformation') THEN
                RAISE EXCEPTION 'migration 0023 cannot be downgraded: there are transformation proposals, '
                                'transformations or key policies, which 0022 cannot hold';
            END IF;
        END $$;
        """
    )
    op.execute(GO_LIVE_GUARD_0018)
    op.drop_column("feed_go_lives", "transformation_ids")
    op.drop_table("feed_key_policies")  # its triggers go with it
    op.drop_table("transformations")
    op.execute("DROP FUNCTION amaze_guard_feed_key_policy()")
    op.execute("DROP FUNCTION amaze_require_transformation_matches_proposal()")
    op.execute("DROP FUNCTION amaze_valid_key_columns(text[])")
    op.execute("DROP FUNCTION amaze_valid_transform_steps(jsonb)")
    op.drop_index("uq_proposals_one_pending_transformation_per_feed_column", table_name="proposals")
    op.drop_constraint(op.f("ck_proposals_names_a_transformation"), "proposals", type_="check")
    op.drop_constraint(op.f("ck_proposals_kind"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_kind"), "proposals", _in(KINDS_0022))
