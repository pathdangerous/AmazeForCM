"""arrival, certification and products: steps 12, 15 and 20

Revision ID: 0019
Revises: 0018

Phase 7 plan, section 6 (guards G12 to G16):
- loads: content_sha256, arrival_outcome, arrival_detail, arrival_profile_id (step 12); the statuses quarantined
  and abandoned; the arrival is set once, while the load is received (G12); the status moves of step 12 and
  15, and a scheduled load moving on only after a passing arrival (G13, trg_loads_a_status replaced).
- load_certifications: step 15's trust score of one scheduled load, append-only, recounted by the database (G14).
- products.feed_id: one product per feed, of a registered feed with a released load, with the owner and
  visibility of its latest registration (G15).
- glossary_runs: the reason structure_change (G16).

Triggers of one event run in name order; the letters fix it (trg_loads_a_status before trg_loads_b_arrival).
Every check on a nullable column spells out NULL. Function bodies and value lists are written out here on
purpose: a migration must not change when code changes. PostgreSQL 14 features only: check constraints,
partial unique indexes, jsonb, cardinality, count(*) FILTER, plpgsql, statement-level TRUNCATE triggers.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

LOAD_STATUSES_0018 = "status IN ('received', 'loaded', 'held', 'failed_rolled_back', 'released')"
LOAD_STATUSES_0019 = ("status IN ('received', 'loaded', 'held', 'failed_rolled_back', 'released', 'quarantined', "
                      "'abandoned')")
REASONS_0016 = "reason IN ('registered', 'requested', 'sent_back')"
REASONS_0019 = "reason IN ('registered', 'requested', 'sent_back', 'structure_change')"

# G12. Nothing about the arrival changes: any other update of the load (a status move, step 15's release) passes.
# Otherwise the four fields are set once, while the load is received, on a scheduled load; a profile goes with
# additive_parked and is one of the load's own feed.
LOAD_ARRIVAL = """
CREATE FUNCTION amaze_guard_load_arrival() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_feed uuid;
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.content_sha256 IS NOT DISTINCT FROM OLD.content_sha256
       AND NEW.arrival_outcome IS NOT DISTINCT FROM OLD.arrival_outcome
       AND NEW.arrival_detail IS NOT DISTINCT FROM OLD.arrival_detail
       AND NEW.arrival_profile_id IS NOT DISTINCT FROM OLD.arrival_profile_id THEN
        RETURN NEW;
    END IF;
    IF NEW.purpose = 'go_live_test' AND (NEW.content_sha256 IS NOT NULL OR NEW.arrival_outcome IS NOT NULL
       OR NEW.arrival_detail IS NOT NULL OR NEW.arrival_profile_id IS NOT NULL) THEN
        RAISE EXCEPTION 'loads: load % is a go_live_test load, and a go_live_test load has no arrival', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.status IS DISTINCT FROM 'received' THEN
            RAISE EXCEPTION 'loads: load % is %, and its arrival is set only while it is received', NEW.id, OLD.status
                USING ERRCODE = 'check_violation';
        END IF;
        IF OLD.content_sha256 IS NOT NULL OR OLD.arrival_outcome IS NOT NULL OR OLD.arrival_detail IS NOT NULL
           OR OLD.arrival_profile_id IS NOT NULL THEN
            RAISE EXCEPTION 'loads: the arrival of load % does not change once set', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.arrival_profile_id IS NOT NULL THEN
        IF NEW.arrival_outcome IS DISTINCT FROM 'additive_parked' THEN
            RAISE EXCEPTION 'loads: load % has an arrival profile, but its arrival outcome is %, not additive_parked',
                NEW.id, COALESCE(NEW.arrival_outcome, 'none') USING ERRCODE = 'check_violation';
        END IF;
        SELECT feed_id INTO p_feed FROM feed_profiles WHERE id = NEW.arrival_profile_id;
        IF p_feed IS DISTINCT FROM NEW.feed_id THEN
            RAISE EXCEPTION 'loads: arrival profile % is not a profile of feed %', NEW.arrival_profile_id,
                NEW.feed_id USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;
"""

# G13: 0018's function, extended. A load is created received and moves once from received: to loaded or
# failed_rolled_back (as 0018; a scheduled one only after on_time_complete or additive_parked), to quarantined
# (resent_quarantined), to held (breaking_held) or to abandoned (a failure and a finish time); the last three
# with no stage, abandoned with no record either. From loaded a scheduled load goes on to released or held with
# a certification of that outcome.
LOAD_STATUS_0019 = """
CREATE OR REPLACE FUNCTION amaze_guard_load_status() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    completed integer;
    failed integer;
    staged integer;
    certified text;
    said text;
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
        IF NEW.purpose IS DISTINCT FROM 'scheduled' THEN
            RAISE EXCEPTION 'loads: load % is a go_live_test load, and only a scheduled load is released or held',
                NEW.id USING ERRCODE = 'check_violation';
        END IF;
        SELECT outcome INTO certified FROM load_certifications WHERE load_id = NEW.id;
        said := CASE NEW.status WHEN 'released' THEN 'released' ELSE 'held_consumers_warned' END;
        IF certified IS DISTINCT FROM said THEN
            RAISE EXCEPTION 'loads: load % cannot be % without a certification that says so', NEW.id, NEW.status
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM 'received' THEN
        RAISE EXCEPTION 'loads: load % is %, and a finished load does not move', OLD.id, OLD.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status NOT IN ('loaded', 'failed_rolled_back', 'quarantined', 'held', 'abandoned') THEN
        RAISE EXCEPTION 'loads: a received load moves only to loaded, failed_rolled_back, quarantined, held or abandoned, not %', NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status IN ('loaded', 'failed_rolled_back') AND NEW.purpose = 'scheduled'
       AND (NEW.arrival_outcome IS NULL OR NEW.arrival_outcome NOT IN ('on_time_complete', 'additive_parked')) THEN
        RAISE EXCEPTION 'loads: scheduled load % has no passing arrival check (its arrival outcome is %)', NEW.id,
            COALESCE(NEW.arrival_outcome, 'none') USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'quarantined' AND NEW.arrival_outcome IS DISTINCT FROM 'resent_quarantined' THEN
        RAISE EXCEPTION 'loads: load % can be quarantined only as resent_quarantined, not %', NEW.id,
            COALESCE(NEW.arrival_outcome, 'none') USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'held' AND NEW.arrival_outcome IS DISTINCT FROM 'breaking_held' THEN
        RAISE EXCEPTION 'loads: load % can be held on arrival only as breaking_held, not %', NEW.id,
            COALESCE(NEW.arrival_outcome, 'none') USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'abandoned' AND (NEW.finished_at IS NULL OR NEW.failure IS NULL OR length(btrim(NEW.failure)) = 0) THEN
        RAISE EXCEPTION 'loads: load % cannot be abandoned without a failure and a finish time', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*), count(*) FILTER (WHERE outcome = 'stage_completed'), count(*) FILTER (WHERE outcome = 'failed_rolled_back')
      INTO staged, completed, failed FROM load_stages WHERE load_id = NEW.id;
    IF NEW.status IN ('quarantined', 'held', 'abandoned') AND staged > 0 THEN
        RAISE EXCEPTION 'loads: load % cannot be % with stages', NEW.id, NEW.status USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'abandoned' AND EXISTS (SELECT 1 FROM standard_records WHERE load_id = NEW.id) THEN
        RAISE EXCEPTION 'loads: load % cannot be abandoned with standard records', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
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

# 0018's function, restored by the downgrade.
LOAD_STATUS_0018 = """
CREATE OR REPLACE FUNCTION amaze_guard_load_status() RETURNS trigger LANGUAGE plpgsql AS $$
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

# G14: a certification is of this feed's loaded scheduled load, counts its standard records and flagged rows as
# they are, scores 100 x unflagged / rows (100.0 for no record), and its outcome follows score and threshold.
LOAD_CERTIFICATION = """
CREATE FUNCTION amaze_guard_load_certification() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    l_feed uuid;
    l_status text;
    l_purpose text;
    n integer;
    flagged integer;
    expected numeric;
    fitting text;
BEGIN
    SELECT feed_id, status, purpose INTO l_feed, l_status, l_purpose FROM loads WHERE id = NEW.load_id;
    IF l_feed IS DISTINCT FROM NEW.feed_id OR l_status IS DISTINCT FROM 'loaded'
       OR l_purpose IS DISTINCT FROM 'scheduled' THEN
        RAISE EXCEPTION 'load_certifications: load % is not a loaded scheduled load of feed %', NEW.load_id,
            NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*), count(*) FILTER (WHERE cardinality(flags) > 0) INTO n, flagged
      FROM standard_records WHERE load_id = NEW.load_id;
    IF NEW."rows" IS DISTINCT FROM n OR NEW.flagged_rows IS DISTINCT FROM flagged THEN
        RAISE EXCEPTION 'load_certifications: load % has % standard records and % flagged, not % and %', NEW.load_id,
            n, flagged, NEW."rows", NEW.flagged_rows USING ERRCODE = 'check_violation';
    END IF;
    expected := CASE WHEN n = 0 THEN 100.0 ELSE round(100.0 * (n - flagged) / n, 1) END;
    IF NEW.trust_score IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'load_certifications: trust score % of load % is not 100 x unflagged / rows (%)',
            NEW.trust_score, NEW.load_id, expected USING ERRCODE = 'check_violation';
    END IF;
    fitting := CASE WHEN NEW.trust_score >= NEW.threshold THEN 'released' ELSE 'held_consumers_warned' END;
    IF NEW.outcome IS DISTINCT FROM fitting THEN
        RAISE EXCEPTION 'load_certifications: outcome % does not fit score % and threshold %', NEW.outcome,
            NEW.trust_score, NEW.threshold USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

# G15: a product with a feed is of a registered feed that has a released load, and has the owner and
# visibility of the feed's latest registration (phase 4 note 5). An update that changes none of the three passes.
PRODUCT_FEED = """
CREATE FUNCTION amaze_guard_product_feed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    f_stage text;
    r_owner text;
    r_visibility text;
BEGIN
    IF NEW.feed_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.feed_id IS NOT DISTINCT FROM OLD.feed_id
       AND NEW.owner_id IS NOT DISTINCT FROM OLD.owner_id AND NEW.visibility IS NOT DISTINCT FROM OLD.visibility THEN
        RETURN NEW;
    END IF;
    SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
    IF f_stage IS DISTINCT FROM 'registered' THEN
        RAISE EXCEPTION 'products: feed % is not registered', NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM loads WHERE feed_id = NEW.feed_id AND status = 'released') THEN
        RAISE EXCEPTION 'products: feed % has no released load', NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    SELECT owner_id, visibility INTO r_owner, r_visibility FROM feed_registrations WHERE feed_id = NEW.feed_id
      ORDER BY version DESC LIMIT 1;
    IF r_owner IS DISTINCT FROM NEW.owner_id OR r_visibility IS DISTINCT FROM NEW.visibility THEN
        RAISE EXCEPTION 'products: owner % and visibility % do not match the latest registration of feed % (% and %)',
            NEW.owner_id, NEW.visibility, NEW.feed_id, r_owner, r_visibility USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

FUNCTIONS = ("amaze_guard_load_arrival", "amaze_guard_load_certification", "amaze_guard_product_feed")


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                     nullable=False)


def upgrade() -> None:
    # ---- loads: the arrival (G12) and the two new statuses
    op.add_column("loads", sa.Column("content_sha256", sa.Text(), nullable=True))
    op.add_column("loads", sa.Column("arrival_outcome", sa.Text(), nullable=True))
    op.add_column("loads", sa.Column("arrival_detail", postgresql.JSONB(), nullable=True))
    op.add_column("loads", sa.Column("arrival_profile_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f("fk_loads_arrival_profile_id_feed_profiles"), "loads", "feed_profiles",
                          ["arrival_profile_id"], ["id"])
    op.create_check_constraint(op.f("ck_loads_content_sha256"), "loads",
                               "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'")
    op.create_check_constraint(
        op.f("ck_loads_arrival_outcome"), "loads",
        "arrival_outcome IS NULL OR arrival_outcome IN ('on_time_complete', 'resent_quarantined', "
        "'additive_parked', 'breaking_held')")
    op.drop_constraint(op.f("ck_loads_status"), "loads", type_="check")
    op.create_check_constraint(op.f("ck_loads_status"), "loads", LOAD_STATUSES_0019)

    # ---- load_certifications (G14)
    op.create_table(
        "load_certifications", _id(), _created_at(),
        sa.Column("load_id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("flagged_rows", sa.Integer(), nullable=False),
        sa.Column("rule_counts", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("trust_score", sa.Numeric(4, 1), nullable=False),
        sa.Column("threshold", sa.Numeric(4, 1), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("warned", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_load_certifications")),
        sa.ForeignKeyConstraint(["load_id"], ["loads.id"], name=op.f("fk_load_certifications_load_id_loads")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_load_certifications_feed_id_feeds")),
        sa.UniqueConstraint("load_id", name=op.f("uq_load_certifications_load_id")),
        sa.CheckConstraint("outcome IN ('released', 'held_consumers_warned')",
                           name=op.f("ck_load_certifications_outcome")),
        sa.CheckConstraint("rows >= 0 AND flagged_rows >= 0 AND flagged_rows <= rows",
                           name=op.f("ck_load_certifications_counts")),
        sa.CheckConstraint("trust_score >= 0 AND trust_score <= 100 AND threshold >= 1 AND threshold <= 100",
                           name=op.f("ck_load_certifications_score")),
    )

    # ---- products: the feed it was created from (G15)
    op.add_column("products", sa.Column("feed_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f("fk_products_feed_id_feeds"), "products", "feeds", ["feed_id"], ["id"])
    op.create_index("uq_products_feed_id", "products", ["feed_id"], unique=True,
                    postgresql_where=sa.text("feed_id IS NOT NULL"))

    # ---- glossary_runs: the reason structure_change (G16)
    op.drop_constraint(op.f("ck_glossary_runs_reason"), "glossary_runs", type_="check")
    op.create_check_constraint(op.f("ck_glossary_runs_reason"), "glossary_runs", REASONS_0019)

    # ---- the functions and their triggers
    for body in (LOAD_ARRIVAL, LOAD_CERTIFICATION, PRODUCT_FEED, LOAD_STATUS_0019):
        op.execute(body)
    op.execute("CREATE TRIGGER trg_loads_b_arrival BEFORE INSERT OR UPDATE ON loads "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_load_arrival()")
    op.execute("CREATE TRIGGER trg_load_certifications_append_only BEFORE UPDATE OR DELETE ON load_certifications "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_load_certifications_no_truncate BEFORE TRUNCATE ON load_certifications "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_load_certifications_b_guard BEFORE INSERT ON load_certifications "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_load_certification()")
    op.execute("CREATE TRIGGER trg_products_b_feed BEFORE INSERT OR UPDATE ON products "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_product_feed()")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM load_certifications)
               OR EXISTS (SELECT 1 FROM products WHERE feed_id IS NOT NULL)
               OR EXISTS (SELECT 1 FROM loads WHERE content_sha256 IS NOT NULL OR arrival_outcome IS NOT NULL
                          OR arrival_detail IS NOT NULL OR arrival_profile_id IS NOT NULL
                          OR status IN ('quarantined', 'abandoned'))
               OR EXISTS (SELECT 1 FROM glossary_runs WHERE reason = 'structure_change') THEN
                RAISE EXCEPTION 'migration 0019 cannot be downgraded: there are certifications, products of a feed, '
                                'loads with an arrival or quarantined or abandoned, or structure_change runs, which '
                                '0018 cannot hold';
            END IF;
        END $$;
        """
    )
    op.execute("DROP TRIGGER trg_products_b_feed ON products")
    op.execute("DROP TRIGGER trg_loads_b_arrival ON loads")
    op.drop_table("load_certifications")  # its triggers go with it
    op.drop_index("uq_products_feed_id", table_name="products")
    op.drop_constraint(op.f("fk_products_feed_id_feeds"), "products", type_="foreignkey")
    op.drop_column("products", "feed_id")
    op.drop_constraint(op.f("ck_glossary_runs_reason"), "glossary_runs", type_="check")
    op.create_check_constraint(op.f("ck_glossary_runs_reason"), "glossary_runs", REASONS_0016)
    op.drop_constraint(op.f("ck_loads_status"), "loads", type_="check")
    op.create_check_constraint(op.f("ck_loads_status"), "loads", LOAD_STATUSES_0018)
    op.drop_constraint(op.f("ck_loads_arrival_outcome"), "loads", type_="check")
    op.drop_constraint(op.f("ck_loads_content_sha256"), "loads", type_="check")
    op.drop_constraint(op.f("fk_loads_arrival_profile_id_feed_profiles"), "loads", type_="foreignkey")
    for column in ("arrival_profile_id", "arrival_detail", "arrival_outcome", "content_sha256"):
        op.drop_column("loads", column)
    op.execute(LOAD_STATUS_0018)
    for function in FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}()")
