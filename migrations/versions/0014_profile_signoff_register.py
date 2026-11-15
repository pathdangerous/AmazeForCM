"""profile, sign-off, register: steps 02 to 04

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-15

Phase 4 (plan, section 6; assumptions 12, 19, 27, 29, 34; decision D1).
- feed_profiles (new): one DuckDB profile of a feed's fetched file per row, versioned per feed.
- feed_signoffs (new): the backend team's sign-off of a profile, versioned per feed; the reasons are kept.
- feed_registrations (new): owner, licence, permitted use, redistribution and visibility of a registered
  feed, versioned per feed (phase 4 writes version 1).
  All three are append-only.
- feeds: a stage guard. A new feed is a candidate; candidate -> registered needs a registration row of
  the same owner; candidate -> rejected needs the feed's latest sign-off to be rejected; nothing else.
  It is an AFTER trigger, so the check constraints of 0004 still answer first for their own cases.
- source_choices: a third release reason, candidate_rejected (ck_source_choices_reason is dropped and
  created again, as 0008 and 0009 did for the status list; never 0009 edited); a selection of a rejected
  feed is refused, and a candidate_rejected release needs a rejected feed.

Triggers of one event run in name order; the letters fix it, as in 0009. Every check on a nullable
column spells out NULL. Function bodies and value lists are written out here on purpose: a migration
must not change when code changes. PostgreSQL 14 features only: check constraints, text[] with
cardinality, jsonb with jsonb_typeof and jsonb_array_length (9.4), format() with %I and EXECUTE ... USING,
triggers on UPDATE OF columns (9.0), AFTER row triggers, plpgsql.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TABLES = ("feed_profiles", "feed_signoffs", "feed_registrations")
REASONS_0009 = "reason IN ('source_down_n_days', 'requester_asked')"
REASONS_0014 = "reason IN ('source_down_n_days', 'requester_asked', 'candidate_rejected')"

# Shared by the three tables: a new row's version is the feed's highest plus one; the first is 1.
VERSION_GUARD = """
CREATE FUNCTION amaze_guard_feed_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    last_version integer;
BEGIN
    EXECUTE format('SELECT max(version) FROM %I WHERE feed_id = $1', TG_TABLE_NAME)
       INTO last_version USING NEW.feed_id;
    IF NEW.version IS DISTINCT FROM COALESCE(last_version, 0) + 1 THEN
        RAISE EXCEPTION '% version % is not the next version of feed %; expected %', TG_TABLE_NAME,
            NEW.version, NEW.feed_id, COALESCE(last_version, 0) + 1
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

PROFILE_FETCH = """
CREATE FUNCTION amaze_require_fetched_file_of_feed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM source_fetches
                    WHERE id = NEW.fetch_id AND feed_id = NEW.feed_id AND outcome = 'fetched') THEN
        RAISE EXCEPTION 'fetch % is not a fetched file of feed %: a profile reads a fetched file of its own feed',
            NEW.fetch_id, NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

SIGNOFF_GUARD = """
CREATE FUNCTION amaze_guard_feed_signoff() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    f_stage text;
    pr_feed uuid;
    pr_outcome text;
    latest_profile uuid;
    p_kind text;
    p_feed uuid;
    p_profile text;
BEGIN
    SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
    IF f_stage IS DISTINCT FROM 'candidate' THEN
        RAISE EXCEPTION 'feed % is %: only a candidate feed is signed off', NEW.feed_id, f_stage
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT feed_id, outcome INTO pr_feed, pr_outcome FROM feed_profiles WHERE id = NEW.profile_id;
    SELECT id INTO latest_profile FROM feed_profiles WHERE feed_id = NEW.feed_id ORDER BY version DESC LIMIT 1;
    IF pr_feed IS DISTINCT FROM NEW.feed_id OR latest_profile IS DISTINCT FROM NEW.profile_id THEN
        RAISE EXCEPTION 'profile % is not the latest profile of feed %', NEW.profile_id, NEW.feed_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF pr_outcome = 'spec_only_low_confidence' AND NEW.outcome = 'meets' THEN
        RAISE EXCEPTION 'a spec-only profile cannot meet the requirement: no data rows were profiled'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT kind, feed_id, payload ->> 'profile_id' INTO p_kind, p_feed, p_profile
      FROM proposals WHERE id = NEW.proposal_id;
    IF p_kind IS DISTINCT FROM 'profile_summary' OR p_feed IS DISTINCT FROM NEW.feed_id
       OR p_profile IS DISTINCT FROM NEW.profile_id::text THEN
        RAISE EXCEPTION 'proposal % is not the profile_summary proposal of profile %', NEW.proposal_id,
            NEW.profile_id USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM approvals WHERE proposal_id = NEW.proposal_id AND role = 'backend_team'
                      AND decided_by = NEW.signed_off_by) THEN
        RAISE EXCEPTION 'the sign-off must name the person who decided proposal % as backend_team, not %',
            NEW.proposal_id, NEW.signed_off_by USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

# Decision D1: approving the summary means the person agrees with its recommended outcome; rejecting it
# means the person chose another one. The approved branch repeats 0005's rules (every role, no rejection).
SIGNOFF_DECISION = """
CREATE FUNCTION amaze_require_signoff_decision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_status text;
    p_roles text[];
    p_recommended text;
    missing_role text;
BEGIN
    SELECT status, required_approver_roles, payload ->> 'recommended_outcome'
      INTO p_status, p_roles, p_recommended FROM proposals WHERE id = NEW.proposal_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'proposal % does not exist', NEW.proposal_id USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF p_recommended IS NULL THEN
        RAISE EXCEPTION 'proposal % names no recommended_outcome', NEW.proposal_id USING ERRCODE = 'check_violation';
    END IF;
    IF p_status = 'approved' THEN
        IF EXISTS (SELECT 1 FROM approvals WHERE proposal_id = NEW.proposal_id AND decision = 'rejected') THEN
            RAISE EXCEPTION 'proposal % has a rejected decision in approvals', NEW.proposal_id
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT r.role INTO missing_role
          FROM unnest(p_roles) WITH ORDINALITY AS r(role, position)
         WHERE NOT EXISTS (SELECT 1 FROM approvals a WHERE a.proposal_id = NEW.proposal_id
                              AND a.role = r.role AND a.decision = 'approved')
         ORDER BY r.position LIMIT 1;
        IF missing_role IS NOT NULL THEN
            RAISE EXCEPTION 'proposal % has no approved decision from role %', NEW.proposal_id, missing_role
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.outcome IS DISTINCT FROM p_recommended THEN
            RAISE EXCEPTION 'an approved summary means the person agreed with its recommended outcome (%), not %',
                p_recommended, NEW.outcome USING ERRCODE = 'check_violation';
        END IF;
    ELSIF p_status = 'rejected' THEN
        IF NOT EXISTS (SELECT 1 FROM approvals WHERE proposal_id = NEW.proposal_id AND decision = 'rejected') THEN
            RAISE EXCEPTION 'proposal % is rejected with no rejected decision in approvals', NEW.proposal_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.outcome IS NOT DISTINCT FROM p_recommended THEN
            RAISE EXCEPTION 'a rejected summary means the person chose another outcome than the recommended %',
                p_recommended USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        RAISE EXCEPTION 'proposal % is %: a sign-off needs a decided summary', NEW.proposal_id, p_status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

REGISTRATION_GUARD = """
CREATE FUNCTION amaze_guard_feed_registration() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    s_feed uuid;
    s_outcome text;
    s_profile uuid;
    latest_signoff uuid;
    latest_profile uuid;
BEGIN
    SELECT feed_id, outcome, profile_id INTO s_feed, s_outcome, s_profile FROM feed_signoffs WHERE id = NEW.signoff_id;
    SELECT id INTO latest_signoff FROM feed_signoffs WHERE feed_id = NEW.feed_id ORDER BY version DESC LIMIT 1;
    SELECT id INTO latest_profile FROM feed_profiles WHERE feed_id = NEW.feed_id ORDER BY version DESC LIMIT 1;
    IF s_feed IS DISTINCT FROM NEW.feed_id OR latest_signoff IS DISTINCT FROM NEW.signoff_id THEN
        RAISE EXCEPTION 'sign-off % is not the latest sign-off of feed %', NEW.signoff_id, NEW.feed_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF s_outcome IS DISTINCT FROM 'meets' AND s_outcome IS DISTINCT FROM 'meets_with_gaps' THEN
        RAISE EXCEPTION 'sign-off % is %: it does not accept the feed', NEW.signoff_id, s_outcome
            USING ERRCODE = 'check_violation';
    END IF;
    IF s_profile IS DISTINCT FROM latest_profile THEN
        RAISE EXCEPTION 'feed % has a newer profile than its sign-off: a newer profile waits for a sign-off',
            NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

FEED_STAGE = """
CREATE FUNCTION amaze_guard_feed_stage() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    r_owner text;
    s_outcome text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.stage IS DISTINCT FROM 'candidate' THEN
            RAISE EXCEPTION 'a new feed is a candidate, not %', NEW.stage USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    IF NEW.stage = OLD.stage THEN
        IF NEW.stage <> 'candidate' AND NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
            RAISE EXCEPTION 'the owner of a % feed is fixed', NEW.stage USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    IF OLD.stage = 'candidate' AND NEW.stage = 'registered' THEN
        SELECT owner_id INTO r_owner FROM feed_registrations WHERE feed_id = NEW.id ORDER BY version DESC LIMIT 1;
        IF r_owner IS NULL OR r_owner IS DISTINCT FROM NEW.owner_id THEN
            RAISE EXCEPTION 'feed % needs a registration of owner % to be registered', NEW.id, NEW.owner_id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    IF OLD.stage = 'candidate' AND NEW.stage = 'rejected' THEN
        SELECT outcome INTO s_outcome FROM feed_signoffs WHERE feed_id = NEW.id ORDER BY version DESC LIMIT 1;
        IF s_outcome IS DISTINCT FROM 'rejected' THEN
            RAISE EXCEPTION 'feed % needs a rejected sign-off to be rejected', NEW.id USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'feed % cannot move from % to %', NEW.id, OLD.stage, NEW.stage USING ERRCODE = 'check_violation';
END $$;
"""

CHOICE_FEED_STAGE = """
CREATE FUNCTION amaze_require_feed_stage_for_choice() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    f_stage text;
BEGIN
    SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the foreign key reports a missing feed
    END IF;
    IF NEW.action = 'selected' AND f_stage = 'rejected' THEN
        RAISE EXCEPTION 'feed % is rejected: it cannot be selected', NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.reason = 'candidate_rejected' AND f_stage IS DISTINCT FROM 'rejected' THEN
        RAISE EXCEPTION 'a candidate_rejected release needs a rejected feed; feed % is %', NEW.feed_id, f_stage
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

FUNCTIONS = ("amaze_require_feed_stage_for_choice", "amaze_guard_feed_stage", "amaze_guard_feed_registration",
             "amaze_require_signoff_decision", "amaze_guard_feed_signoff", "amaze_require_fetched_file_of_feed",
             "amaze_guard_feed_version")


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                     nullable=False)


def _feed(table: str) -> list:
    return [sa.Column("feed_id", sa.Uuid(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f(f"fk_{table}_feed_id_feeds")),
            sa.UniqueConstraint("feed_id", "version", name=op.f(f"uq_{table}_feed_id_version")),
            sa.CheckConstraint("version >= 1", name=op.f(f"ck_{table}_version")),
            sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}"))]


def _trigger(table: str, name: str, function: str, when: str = "BEFORE INSERT") -> None:
    op.execute(f"CREATE TRIGGER trg_{table}_{name} {when} ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()")


def upgrade() -> None:
    for body in (VERSION_GUARD, PROFILE_FETCH, SIGNOFF_GUARD, SIGNOFF_DECISION, REGISTRATION_GUARD, FEED_STAGE,
                 CHOICE_FEED_STAGE):
        op.execute(body)

    op.create_table(
        "feed_profiles", _id(), _created_at(), *_feed("feed_profiles"),
        sa.Column("fetch_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("columns", postgresql.JSONB(), nullable=False),
        sa.Column("coverage", postgresql.JSONB(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("profiled_by", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["fetch_id"], ["source_fetches.id"], name=op.f("fk_feed_profiles_fetch_id_source_fetches")),
        sa.CheckConstraint("outcome IN ('profiled', 'spec_only_low_confidence')", name=op.f("ck_feed_profiles_outcome")),
        sa.CheckConstraint(
            "outcome <> 'profiled' OR (row_count IS NOT NULL AND row_count >= 1 AND detail IS NULL "
            "AND jsonb_typeof(columns) = 'array' AND jsonb_array_length(columns) >= 1)",
            name=op.f("ck_feed_profiles_profiled_has_rows")),
        sa.CheckConstraint(
            "outcome <> 'spec_only_low_confidence' OR (COALESCE(row_count, 0) = 0 AND detail IS NOT NULL "
            "AND length(btrim(detail)) > 0)",
            name=op.f("ck_feed_profiles_spec_only_has_a_reason")),
        sa.CheckConstraint(
            "jsonb_typeof(columns) = 'array' AND jsonb_typeof(coverage) = 'array' AND jsonb_typeof(spec) = 'object'",
            name=op.f("ck_feed_profiles_json_shapes")),
        sa.CheckConstraint("length(btrim(engine)) > 0 AND length(btrim(profiled_by)) > 0",
                           name=op.f("ck_feed_profiles_text_present")),
    )
    op.create_table(
        "feed_signoffs", _id(), _created_at(), *_feed("feed_signoffs"),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("gaps", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("reasons", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("signed_off_by", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["feed_profiles.id"],
                                name=op.f("fk_feed_signoffs_profile_id_feed_profiles")),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], name=op.f("fk_feed_signoffs_proposal_id_proposals")),
        sa.UniqueConstraint("profile_id", name=op.f("uq_feed_signoffs_profile_id")),
        sa.UniqueConstraint("proposal_id", name=op.f("uq_feed_signoffs_proposal_id")),
        sa.CheckConstraint("outcome IN ('meets', 'meets_with_gaps', 'rejected')", name=op.f("ck_feed_signoffs_outcome")),
        sa.CheckConstraint(
            "(outcome = 'meets' AND cardinality(gaps) = 0 AND cardinality(reasons) = 0) OR "
            "(outcome = 'meets_with_gaps' AND cardinality(gaps) >= 1 AND cardinality(reasons) = 0) OR "
            "(outcome = 'rejected' AND cardinality(reasons) >= 1 AND cardinality(gaps) = 0)",
            name=op.f("ck_feed_signoffs_gaps_and_reasons_fit")),
        sa.CheckConstraint("length(btrim(signed_off_by)) > 0", name=op.f("ck_feed_signoffs_signed_off_by")),
    )
    op.create_table(
        "feed_registrations", _id(), _created_at(), *_feed("feed_registrations"),
        sa.Column("signoff_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("licence", sa.Text(), nullable=False),
        sa.Column("permitted_use", sa.Text(), nullable=False),
        sa.Column("redistribution", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("registered_by", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["signoff_id"], ["feed_signoffs.id"],
                                name=op.f("fk_feed_registrations_signoff_id_feed_signoffs")),
        sa.CheckConstraint("visibility IN ('open', 'restricted')", name=op.f("ck_feed_registrations_visibility")),
        sa.CheckConstraint(
            "length(btrim(owner_id)) > 0 AND length(btrim(licence)) > 0 AND length(btrim(permitted_use)) > 0 "
            "AND length(btrim(redistribution)) > 0 AND length(btrim(registered_by)) > 0",
            name=op.f("ck_feed_registrations_text_present")),
    )
    for table in TABLES:
        op.execute(f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
        op.execute(f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()")
        _trigger(table, "a_version", "amaze_guard_feed_version")
    _trigger("feed_profiles", "b_fetch", "amaze_require_fetched_file_of_feed")
    _trigger("feed_signoffs", "b_guard", "amaze_guard_feed_signoff")
    _trigger("feed_signoffs", "c_decision", "amaze_require_signoff_decision")
    _trigger("feed_registrations", "b_guard", "amaze_guard_feed_registration")
    _trigger("feeds", "a_stage", "amaze_guard_feed_stage", when="AFTER INSERT OR UPDATE OF stage, owner_id")
    _trigger("source_choices", "f_feed_stage", "amaze_require_feed_stage_for_choice")

    op.drop_constraint(op.f("ck_source_choices_reason"), "source_choices", type_="check")
    op.create_check_constraint(op.f("ck_source_choices_reason"), "source_choices", REASONS_0014)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM feed_profiles) OR EXISTS (SELECT 1 FROM feed_signoffs)
               OR EXISTS (SELECT 1 FROM feed_registrations)
               OR EXISTS (SELECT 1 FROM source_choices WHERE reason = 'candidate_rejected') THEN
                RAISE EXCEPTION 'migration 0014 cannot be downgraded: there are profiles, sign-offs, registrations '
                                'or candidate_rejected releases, which 0013 cannot hold';
            END IF;
        END $$;
        """
    )
    op.drop_constraint(op.f("ck_source_choices_reason"), "source_choices", type_="check")
    op.create_check_constraint(op.f("ck_source_choices_reason"), "source_choices", REASONS_0009)
    op.execute("DROP TRIGGER trg_source_choices_f_feed_stage ON source_choices")
    op.execute("DROP TRIGGER trg_feeds_a_stage ON feeds")
    for table in reversed(TABLES):
        op.drop_table(table)  # its triggers go with it
    for function in FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}()")
