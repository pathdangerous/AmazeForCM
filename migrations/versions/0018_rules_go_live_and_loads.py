"""rules, go-live and loads: steps 09, 11 and 13

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-17

Phase 6 plan, section 6 (guards G1 to G11; assumptions 31 and 32).
- proposals: a rule proposal has a feed and names its rule_key and sql (G1); one pending rule proposal per
  feed and rule key, and one pending review_summary per feed (G2).
- rules: one row per proposal (G3); a row matches its approved proposal: feed, key, SQL, description, the key's
  next version, a registered feed (G5, phase 5 note 5; trg_rules_b_matches_proposal). The 0004 and 0005
  trigger runs first (trg_rules_approved_proposal sorts before trg_rules_b_).
- feed_go_lives (new): the owner's approval of one configuration (mapping ids, rule ids) and one test load,
  versioned per feed, append-only (G4); trg_feed_go_lives_b_guard (G6).
- load_stages (new): one row per stage of a load (raw, curated, validated, standard), append-only (G4), written
  in order while the load is received (G10).
- standard_records (new): one row per file row of a loaded file, append-only (G4), written only for a
  received load of the same feed (G11).
- loads: purpose, fetch_id, go_live_id, finished_at, failed_stage, failure; the status loaded; the purpose fits
  the go-live (G7); the status moves received -> loaded or failed_rolled_back, with its proof (G8); one
  received load per feed (G9).

Triggers of one event run in name order; the letters fix it, as in 0009 and 0014. Every check on a nullable
column spells out NULL. Function bodies and value lists are written out here on purpose: a migration must not
change when code changes. PostgreSQL 14 features only: check constraints, partial unique indexes on an
expression, jsonb with jsonb_typeof and jsonb_array_elements_text, array_position (9.5), count(*) FILTER (9.4),
uuid[], plpgsql, statement-level TRUNCATE triggers.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

LOAD_STATUSES_0004 = "status IN ('received', 'held', 'failed_rolled_back', 'released')"
LOAD_STATUSES_0018 = "status IN ('received', 'loaded', 'held', 'failed_rolled_back', 'released')"

# G1: a rule proposal has a feed and names its rule_key and sql (non-blank JSON strings). jsonb_typeof of a
# missing key or a JSON null is NULL or 'null': COALESCE keeps either from passing the check.
NAMES_A_RULE = ("kind <> 'rule' "
                "OR (feed_id IS NOT NULL "
                "AND COALESCE(jsonb_typeof(payload -> 'rule_key') = 'string', false) "
                "AND length(btrim(payload ->> 'rule_key')) > 0 "
                "AND COALESCE(jsonb_typeof(payload -> 'sql') = 'string', false) "
                "AND length(btrim(payload ->> 'sql')) > 0)")

RULE_MATCHES = """
CREATE FUNCTION amaze_require_rule_matches_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_feed uuid;
    p_payload jsonb;
    latest integer;
    f_stage text;
BEGIN
    SELECT feed_id, payload INTO p_feed, p_payload FROM proposals WHERE id = NEW.proposal_id;
    IF p_feed IS DISTINCT FROM NEW.feed_id THEN
        RAISE EXCEPTION 'rule: proposal % is for feed %, not %', NEW.proposal_id, COALESCE(p_feed::text, 'no feed'),
            NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    IF (p_payload ->> 'rule_key') IS DISTINCT FROM NEW.rule_key THEN
        RAISE EXCEPTION 'rule: proposal % is for rule %, not %', NEW.proposal_id,
            COALESCE(p_payload ->> 'rule_key', 'none'), NEW.rule_key USING ERRCODE = 'check_violation';
    END IF;
    IF (p_payload ->> 'sql') IS DISTINCT FROM NEW.sql THEN
        RAISE EXCEPTION 'rule: proposal % holds other SQL than the row', NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF (p_payload ->> 'description') IS DISTINCT FROM NEW.description THEN
        RAISE EXCEPTION 'rule: proposal % holds another description than the row', NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(version) INTO latest FROM rules WHERE feed_id = NEW.feed_id AND rule_key = NEW.rule_key;
    IF NEW.version IS DISTINCT FROM COALESCE(latest, 0) + 1 THEN
        RAISE EXCEPTION 'rule: version % of rule % is not the next one (%)', NEW.version, NEW.rule_key,
            COALESCE(latest, 0) + 1 USING ERRCODE = 'check_violation';
    END IF;
    SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
    IF f_stage IS DISTINCT FROM 'registered' THEN
        RAISE EXCEPTION 'rule: feed % is %, not registered', NEW.feed_id, COALESCE(f_stage, 'missing')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

# G6. The ids are compared as sorted arrays; a payload key that is missing or not an array reads as empty, and a
# missing test_load_id as NULL, which IS DISTINCT FROM never lets match the row's.
GO_LIVE_GUARD = """
CREATE FUNCTION amaze_guard_feed_go_live() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_kind text;
    p_feed uuid;
    p_status text;
    p_payload jsonb;
    p_mappings uuid[];
    p_rules uuid[];
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

# G8: a load is created received and moves once, to loaded (four completed stages) or failed_rolled_back (one
# failed stage, and no standard record). From loaded it may go on to held or released, which step 15 uses in
# phase 7 and the consumer side already reads (a deviation from the plan, which refused every other move).
LOAD_STATUS = """
CREATE FUNCTION amaze_guard_load_status() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    completed integer;
    failed integer;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'received' THEN
            RAISE EXCEPTION 'loads: a new load is received, not %', NEW.status USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;
    END IF;
    IF OLD.status = 'loaded' AND NEW.status IN ('held', 'released') THEN
        RETURN NEW;  -- certification (step 15, phase 7); 0004 already lists both, and phase 1 code and tests use them
    END IF;
    IF OLD.status IS DISTINCT FROM 'received' THEN
        RAISE EXCEPTION 'loads: load % is %, and a finished load does not move', OLD.id, OLD.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status NOT IN ('loaded', 'failed_rolled_back') THEN
        RAISE EXCEPTION 'loads: a received load moves only to loaded or failed_rolled_back, not %', NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*) FILTER (WHERE outcome = 'stage_completed'), count(*) FILTER (WHERE outcome = 'failed_rolled_back')
      INTO completed, failed FROM load_stages WHERE load_id = NEW.id;
    IF NEW.status = 'loaded' AND completed <> 4 THEN
        RAISE EXCEPTION 'loads: load % cannot be loaded: % of its four stages completed', NEW.id, completed
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'failed_rolled_back' THEN
        IF failed <> 1 THEN
            RAISE EXCEPTION 'loads: load % cannot fail without a failed stage', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM standard_records WHERE load_id = NEW.id) THEN
            RAISE EXCEPTION 'loads: load % cannot fail with standard records', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;
"""

# G10: a stage row is written while the load is received, after every earlier stage completed. A missing load is
# left to the foreign key, an unknown stage to ck_load_stages_stage.
LOAD_STAGE = """
CREATE FUNCTION amaze_guard_load_stage() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    l_status text;
    slot integer := array_position(ARRAY['raw', 'curated', 'validated', 'standard'], NEW.stage);
    written integer;
    completed integer;
BEGIN
    SELECT status INTO l_status FROM loads WHERE id = NEW.load_id;
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;
    IF l_status IS DISTINCT FROM 'received' THEN
        RAISE EXCEPTION 'load_stages: load % is %, and stages are written only while it is received', NEW.load_id,
            l_status USING ERRCODE = 'check_violation';
    END IF;
    IF slot IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT count(*), count(*) FILTER (WHERE outcome = 'stage_completed') INTO written, completed
      FROM load_stages WHERE load_id = NEW.load_id;
    IF written <> slot - 1 OR completed <> written THEN
        RAISE EXCEPTION 'load_stages: stage % of load % comes out of order', NEW.stage, NEW.load_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

# G11
STANDARD_RECORD = """
CREATE FUNCTION amaze_guard_standard_record() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    l_status text;
    l_feed uuid;
BEGIN
    SELECT status, feed_id INTO l_status, l_feed FROM loads WHERE id = NEW.load_id;
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;
    IF l_status IS DISTINCT FROM 'received' THEN
        RAISE EXCEPTION 'standard_records: load % is %, and records are written only while it is received',
            NEW.load_id, l_status USING ERRCODE = 'check_violation';
    END IF;
    IF l_feed IS DISTINCT FROM NEW.feed_id THEN
        RAISE EXCEPTION 'standard_records: load % belongs to feed %, not %', NEW.load_id, l_feed, NEW.feed_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

NEW_TABLES = ("feed_go_lives", "load_stages", "standard_records")
FUNCTIONS = ("amaze_guard_standard_record", "amaze_guard_load_stage", "amaze_guard_load_status",
             "amaze_guard_feed_go_live", "amaze_require_rule_matches_proposal")


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                     nullable=False)


def upgrade() -> None:
    for body in (RULE_MATCHES, GO_LIVE_GUARD, LOAD_STATUS, LOAD_STAGE, STANDARD_RECORD):
        op.execute(body)

    # ---- proposals and rules: G1, G2, G3, G5
    op.create_check_constraint(op.f("ck_proposals_names_a_rule"), "proposals", NAMES_A_RULE)
    op.create_index("uq_proposals_one_pending_rule_per_feed_key", "proposals",
                    ["feed_id", sa.text("(payload ->> 'rule_key')")], unique=True,
                    postgresql_where=sa.text("status = 'pending' AND kind = 'rule'"))
    op.create_index("uq_proposals_one_pending_review_summary_per_feed", "proposals", ["feed_id"], unique=True,
                    postgresql_where=sa.text("status = 'pending' AND kind = 'review_summary'"))
    op.create_index("uq_rules_proposal_id", "rules", ["proposal_id"], unique=True)
    op.execute("CREATE TRIGGER trg_rules_b_matches_proposal BEFORE INSERT ON rules "
               "FOR EACH ROW EXECUTE FUNCTION amaze_require_rule_matches_proposal()")

    # ---- loads: new columns, the status loaded, G7, G8, G9 (the go_live_id foreign key follows feed_go_lives)
    op.add_column("loads", sa.Column("purpose", sa.Text(), nullable=False))
    op.add_column("loads", sa.Column("fetch_id", sa.Uuid(), nullable=True))
    op.add_column("loads", sa.Column("go_live_id", sa.Uuid(), nullable=True))
    op.add_column("loads", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("loads", sa.Column("failed_stage", sa.Text(), nullable=True))
    op.add_column("loads", sa.Column("failure", sa.Text(), nullable=True))
    op.create_foreign_key(op.f("fk_loads_fetch_id_source_fetches"), "loads", "source_fetches", ["fetch_id"], ["id"])
    op.drop_constraint(op.f("ck_loads_status"), "loads", type_="check")
    op.create_check_constraint(op.f("ck_loads_status"), "loads", LOAD_STATUSES_0018)
    op.create_check_constraint(
        op.f("ck_loads_purpose_fits"), "loads",
        "(purpose = 'scheduled' AND go_live_id IS NOT NULL) OR (purpose = 'go_live_test' AND go_live_id IS NULL)")
    op.create_index("uq_loads_one_received_per_feed", "loads", ["feed_id"], unique=True,
                    postgresql_where=sa.text("status = 'received'"))
    op.execute("CREATE TRIGGER trg_loads_a_status BEFORE INSERT OR UPDATE ON loads "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_load_status()")

    # ---- the three new tables
    op.create_table(
        "feed_go_lives", _id(), _created_at(),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=False),
        sa.Column("mapping_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("rule_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("test_load_id", sa.Uuid(), nullable=False),
        sa.Column("hypercare_until", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_go_lives")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_feed_go_lives_feed_id_feeds")),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], name=op.f("fk_feed_go_lives_proposal_id_proposals")),
        sa.ForeignKeyConstraint(["test_load_id"], ["loads.id"], name=op.f("fk_feed_go_lives_test_load_id_loads")),
        sa.UniqueConstraint("feed_id", "version", name=op.f("uq_feed_go_lives_feed_id_version")),
        sa.UniqueConstraint("proposal_id", name=op.f("uq_feed_go_lives_proposal_id")),
    )
    op.create_foreign_key(op.f("fk_loads_go_live_id_feed_go_lives"), "loads", "feed_go_lives", ["go_live_id"], ["id"])
    op.create_table(
        "load_stages", _id(), _created_at(),
        sa.Column("load_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("rows_in", sa.Integer(), nullable=False),
        sa.Column("rows_out", sa.Integer(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_load_stages")),
        sa.ForeignKeyConstraint(["load_id"], ["loads.id"], name=op.f("fk_load_stages_load_id_loads")),
        sa.UniqueConstraint("load_id", "stage", name=op.f("uq_load_stages_load_id_stage")),
        sa.CheckConstraint("stage IN ('raw', 'curated', 'validated', 'standard')", name=op.f("ck_load_stages_stage")),
        sa.CheckConstraint("outcome IN ('stage_completed', 'failed_rolled_back')",
                           name=op.f("ck_load_stages_outcome")),
        sa.CheckConstraint("rows_in >= 0 AND rows_out >= 0", name=op.f("ck_load_stages_counts")),
    )
    op.create_table(
        "standard_records", _id(), _created_at(),
        sa.Column("load_id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("values", postgresql.JSONB(), nullable=False),
        sa.Column("flags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_standard_records")),
        sa.ForeignKeyConstraint(["load_id"], ["loads.id"], name=op.f("fk_standard_records_load_id_loads")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_standard_records_feed_id_feeds")),
        sa.CheckConstraint("row_number >= 1", name=op.f("ck_standard_records_row_number")),
    )
    op.create_index("ix_standard_records_load_id", "standard_records", ["load_id"])

    # ---- G4 and the order, version and ownership guards of the new tables
    for table in NEW_TABLES:
        op.execute(f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
        op.execute(f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_feed_go_lives_b_guard BEFORE INSERT ON feed_go_lives "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_feed_go_live()")
    op.execute("CREATE TRIGGER trg_load_stages_b_order BEFORE INSERT ON load_stages "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_load_stage()")
    op.execute("CREATE TRIGGER trg_standard_records_b_load BEFORE INSERT ON standard_records "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_standard_record()")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM feed_go_lives) OR EXISTS (SELECT 1 FROM load_stages)
               OR EXISTS (SELECT 1 FROM standard_records) OR EXISTS (SELECT 1 FROM loads)
               OR EXISTS (SELECT 1 FROM rules)
               OR EXISTS (SELECT 1 FROM proposals WHERE kind IN ('rule', 'review_summary')) THEN
                RAISE EXCEPTION 'migration 0018 cannot be downgraded: there are go-lives, load stages, standard '
                                'records, loads, rules, or rule and review_summary proposals, which 0017 cannot hold';
            END IF;
        END $$;
        """
    )
    op.drop_table("standard_records")  # its triggers go with it
    op.drop_table("load_stages")
    op.drop_constraint(op.f("fk_loads_go_live_id_feed_go_lives"), "loads", type_="foreignkey")
    op.drop_table("feed_go_lives")
    op.execute("DROP TRIGGER trg_loads_a_status ON loads")
    op.drop_index("uq_loads_one_received_per_feed", table_name="loads")
    op.drop_constraint(op.f("ck_loads_purpose_fits"), "loads", type_="check")
    op.drop_constraint(op.f("ck_loads_status"), "loads", type_="check")
    op.create_check_constraint(op.f("ck_loads_status"), "loads", LOAD_STATUSES_0004)
    op.drop_constraint(op.f("fk_loads_fetch_id_source_fetches"), "loads", type_="foreignkey")
    for column in ("failure", "failed_stage", "finished_at", "go_live_id", "fetch_id", "purpose"):
        op.drop_column("loads", column)
    op.execute("DROP TRIGGER trg_rules_b_matches_proposal ON rules")
    op.drop_index("uq_rules_proposal_id", table_name="rules")
    op.drop_index("uq_proposals_one_pending_review_summary_per_feed", table_name="proposals")
    op.drop_index("uq_proposals_one_pending_rule_per_feed_key", table_name="proposals")
    op.drop_constraint(op.f("ck_proposals_names_a_rule"), "proposals", type_="check")
    for function in FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}()")
