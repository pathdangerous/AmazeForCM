"""exception fixes: the exception_fix proposal, exception_resolutions, waived records, load reruns

Revision ID: 0034
Revises: 0033

Phase 7.14, T2 (plan: docs/plans/phase-7.14.md, sections 2.2, 2.4, 2.6 and 2.7). An agent (ExceptionFixAgent) or a
person proposes one fix per exception group of a held load; people decide; only then is anything recorded. An agent
never changes data (rule 1) and no record is updated: a waiver is applied by the engine to the records of a NEW load
(a rerun of the held file), never to the held load's records.

- proposals.kind gains 'exception_fix' (ck_proposals_kind, built from 0029's list), and a person may add one
  (ck_proposals_manual_kinds). ck_proposals_exception_fix: it has a feed, its payload names the load, rule key,
  element keys, pattern, group key, action and reason, and its approvers fit the action (waive: data_steward then
  data_owner; refer: data_steward). One pending exception_fix per feed, load and group.
- exception_resolutions (new, append-only): one approved exception_fix, recorded. trg_exception_resolutions_a_guard:
  the proposal is an approved exception_fix of the feed with every approver step done, the row matches its payload,
  applied_by follows its origin, decided_by is the last step's person, the load is a held load of the feed.
- trg_approvals_d_waiver_owner (new, beside 0024's and 0032's): the data_owner step of an exception_fix is decided only
  by the feed's registered owner (its latest registration).
- standard_records.waived (new, text[], default '{}'): rule keys a waiver turned from a flag into a waived exception.
  ck_standard_records_flagged_or_waived: no key in both. trg_standard_records_c_waived (D4): each waived key has a
  current waiver of the feed for that rule whose pattern is amaze_value_pattern(values, its element keys) (0033).
- load_certifications.waived_rows and waived_counts (D2). amaze_guard_load_certification() (0019, G14) new version (D1):
  it also recounts the rows with no flag and a waived key, and refuses flagged + waived > rows. Score and outcome
  checks are unchanged: a waived key is not in flags, so 100 x unflagged / rows already counts it as unflagged.
- loads.rerun_of_load_id and loads.rerun_reason (Q3): a rerun is a scheduled load of a held load's own file, with a
  reason. trg_loads_c_rerun: at insert, the target is a held load of the same feed certified held_consumers_warned and
  the new load is scheduled with the same file; afterwards both columns never change.
- amaze_delete_feed() (as 0032 left it) also deletes the feed's exception_resolutions, before its records and loads.

Downgrade stops, naming 0034, while a resolution, an exception_fix proposal, a rerun load, a waived record or a
certification with waived rows exists. PostgreSQL 14 features only.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

HERE = Path(__file__).resolve().parent


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"m{name}", next(HERE.glob(f"{name}_*.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _swap(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"0034 expected exactly one: {old[:70]!r}"
    return text.replace(old, new)


def _kinds_0029() -> tuple[str, ...]:
    return (*_module("0029")._kinds_0023(), "profile_action")


MANUAL_0029 = ("glossary_element", "mapping", "transformation", "rule", "profile_action")
FIX_KEYS = ("load_id", "rule_key", "element_keys", "pattern", "group_key", "action", "reason")

# Spelt out so that a missing or JSON-null field is false, never NULL (a CHECK that is NULL passes).
EXCEPTION_FIX = (
    "kind <> 'exception_fix' OR (feed_id IS NOT NULL "
    f"AND payload ?& ARRAY[{', '.join(repr(k) for k in FIX_KEYS)}] "
    "AND COALESCE(jsonb_typeof(payload -> 'element_keys') = 'array', false) "
    "AND COALESCE(length(btrim(payload ->> 'reason')) > 0, false) "
    "AND COALESCE((payload ->> 'action' = 'waive' "
    "AND required_approver_roles = ARRAY['data_steward', 'data_owner']::text[]) "
    "OR (payload ->> 'action' = 'refer' AND required_approver_roles = ARRAY['data_steward']::text[]), false))")

RESOLUTION_GUARD = """
CREATE FUNCTION amaze_guard_exception_resolution() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p proposals%ROWTYPE;
    done integer;
    last_person text;
    l_feed uuid;
    l_status text;
BEGIN
    SELECT * INTO p FROM proposals WHERE id = NEW.proposal_id;
    IF p.id IS NULL OR p.kind IS DISTINCT FROM 'exception_fix' OR p.feed_id IS DISTINCT FROM NEW.feed_id THEN
        RAISE EXCEPTION 'exception_resolutions: proposal % is not an exception_fix of feed %', NEW.proposal_id,
            NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*) INTO done FROM approvals WHERE proposal_id = p.id AND decision = 'approved';
    IF p.status IS DISTINCT FROM 'approved' OR done < cardinality(p.required_approver_roles) THEN
        RAISE EXCEPTION 'exception_resolutions: proposal % is %, with % of % approvals: a fix is recorded only once '
            'every approver decided it', p.id, p.status, done, cardinality(p.required_approver_roles)
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.action IS DISTINCT FROM (CASE p.payload ->> 'action' WHEN 'waive' THEN 'waived' WHEN 'refer' THEN 'referred' END)
       OR NEW.load_id::text IS DISTINCT FROM p.payload ->> 'load_id'
       OR NEW.rule_key IS DISTINCT FROM p.payload ->> 'rule_key'
       OR NEW.pattern IS DISTINCT FROM p.payload ->> 'pattern'
       OR NEW.group_key IS DISTINCT FROM p.payload ->> 'group_key'
       OR NEW.reason IS DISTINCT FROM p.payload ->> 'reason'
       OR NEW.element_keys IS DISTINCT FROM ARRAY(SELECT jsonb_array_elements_text(p.payload -> 'element_keys')) THEN
        RAISE EXCEPTION 'exception_resolutions: the row does not match its proposal %', p.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.applied_by IS DISTINCT FROM (CASE p.origin WHEN 'agent' THEN 'agent' ELSE 'manual' END) THEN
        RAISE EXCEPTION 'exception_resolutions: proposal % is %, so the fix was applied by %, not %', p.id, p.origin,
            CASE p.origin WHEN 'agent' THEN 'agent' ELSE 'manual' END, NEW.applied_by USING ERRCODE = 'check_violation';
    END IF;
    SELECT decided_by INTO last_person FROM approvals WHERE proposal_id = p.id AND decision = 'approved'
     ORDER BY step DESC LIMIT 1;
    IF NEW.decided_by IS DISTINCT FROM last_person THEN
        RAISE EXCEPTION 'exception_resolutions: proposal % was decided last by %, not %', p.id, last_person,
            NEW.decided_by USING ERRCODE = 'check_violation';
    END IF;
    SELECT feed_id, status INTO l_feed, l_status FROM loads WHERE id = NEW.load_id;
    IF l_feed IS DISTINCT FROM NEW.feed_id OR l_status IS DISTINCT FROM 'held' THEN
        RAISE EXCEPTION 'exception_resolutions: load % is not a held load of feed %', NEW.load_id, NEW.feed_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

WAIVER_OWNER = """
CREATE FUNCTION amaze_guard_waiver_owner() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_kind text;
    p_feed uuid;
    owner text;
BEGIN
    IF NEW.role IS DISTINCT FROM 'data_owner' THEN
        RETURN NEW;
    END IF;
    SELECT kind, feed_id INTO p_kind, p_feed FROM proposals WHERE id = NEW.proposal_id;
    IF p_kind IS DISTINCT FROM 'exception_fix' THEN
        RETURN NEW;
    END IF;
    SELECT owner_id INTO owner FROM feed_registrations WHERE feed_id = p_feed ORDER BY version DESC LIMIT 1;
    IF NEW.decided_by IS DISTINCT FROM owner THEN
        RAISE EXCEPTION 'approval: the data owner step of exception fix % is decided by the feed''s registered owner %, '
            'not %', NEW.proposal_id, owner, NEW.decided_by USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

# A waiver is current while no later resolution of the same feed, rule and pattern exists (a referral ends it).
WAIVED_RECORD = """
CREATE FUNCTION amaze_guard_waived_record() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    k text;
BEGIN
    FOREACH k IN ARRAY NEW.waived LOOP
        IF NOT EXISTS (
            SELECT 1 FROM exception_resolutions r
             WHERE r.feed_id = NEW.feed_id AND r.rule_key = k AND r.action = 'waived'
               AND r.pattern = amaze_value_pattern(NEW."values", r.element_keys)
               AND NOT EXISTS (SELECT 1 FROM exception_resolutions later
                                WHERE later.feed_id = r.feed_id AND later.rule_key = r.rule_key
                                  AND later.pattern = r.pattern AND later.created_at > r.created_at)) THEN
            RAISE EXCEPTION 'standard_records: row % of load % waives %, but no current waiver of feed % matches it',
                NEW.row_number, NEW.load_id, k, NEW.feed_id USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;
"""

LOAD_RERUN = """
CREATE FUNCTION amaze_guard_load_rerun() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    t loads%ROWTYPE;
    certified text;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.rerun_of_load_id IS DISTINCT FROM OLD.rerun_of_load_id
           OR NEW.rerun_reason IS DISTINCT FROM OLD.rerun_reason THEN
            RAISE EXCEPTION 'loads: the rerun of load % is set once, when it is created', OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.rerun_of_load_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO t FROM loads WHERE id = NEW.rerun_of_load_id;
    SELECT outcome INTO certified FROM load_certifications WHERE load_id = t.id;
    IF t.feed_id IS DISTINCT FROM NEW.feed_id OR t.status IS DISTINCT FROM 'held'
       OR certified IS DISTINCT FROM 'held_consumers_warned' OR NEW.purpose IS DISTINCT FROM 'scheduled' THEN
        RAISE EXCEPTION 'loads: a rerun is a scheduled load of a held load of the same feed (load % is %, certified %)',
            NEW.rerun_of_load_id, COALESCE(t.status, 'missing'), COALESCE(certified, 'not at all')
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.file_ref IS DISTINCT FROM t.file_ref THEN
        RAISE EXCEPTION 'loads: a rerun loads the held load''s own file %, not %', t.file_ref, NEW.file_ref
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def _certification_0019() -> str:
    return _module("0019").LOAD_CERTIFICATION.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def _certification_0034() -> str:
    text = _swap(_certification_0019(), "    flagged integer;\n", "    flagged integer;\n    n_waived integer;\n")
    text = _swap(text, """    SELECT count(*), count(*) FILTER (WHERE cardinality(flags) > 0) INTO n, flagged
      FROM standard_records WHERE load_id = NEW.load_id;
""", """    SELECT count(*), count(*) FILTER (WHERE cardinality(flags) > 0),
           count(*) FILTER (WHERE cardinality(flags) = 0 AND cardinality(standard_records.waived) > 0)
      INTO n, flagged, n_waived
      FROM standard_records WHERE load_id = NEW.load_id;
    IF NEW.waived_rows IS DISTINCT FROM n_waived THEN
        RAISE EXCEPTION 'load_certifications: load % has % waived rows (no flag, a waived exception), not %',
            NEW.load_id, n_waived, NEW.waived_rows USING ERRCODE = 'check_violation';
    END IF;
""")
    return text


def _delete_function_0032() -> str:
    return _module("0032")._delete_function_0032()


def _delete_function_0034() -> str:
    block = _module("0027")._delete_block("exception_resolutions", "feed_id = p_feed", "exception_resolutions")
    return _swap(_delete_function_0032(), "    DELETE FROM standard_records WHERE",
                 block + "    DELETE FROM standard_records WHERE")


def upgrade() -> None:
    op.drop_constraint(op.f("ck_proposals_kind"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_kind"), "proposals", _in("kind", (*_kinds_0029(), "exception_fix")))
    op.drop_constraint(op.f("ck_proposals_manual_kinds"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_manual_kinds"), "proposals",
                               f"origin = 'agent' OR ({_in('kind', (*MANUAL_0029, 'exception_fix'))} "
                               "AND feed_id IS NOT NULL)")
    op.create_check_constraint(op.f("ck_proposals_exception_fix"), "proposals", EXCEPTION_FIX)
    op.create_index("uq_proposals_one_pending_exception_fix", "proposals",
                    ["feed_id", sa.text("(payload ->> 'load_id')"), sa.text("(payload ->> 'group_key')")], unique=True,
                    postgresql_where=sa.text("status = 'pending' AND kind = 'exception_fix'"))

    op.create_table(
        "exception_resolutions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("load_id", sa.Uuid(), nullable=False),
        sa.Column("rule_key", sa.Text(), nullable=False),
        sa.Column("element_keys", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("group_key", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("applied_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exception_resolutions")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_exception_resolutions_feed_id_feeds")),
        sa.ForeignKeyConstraint(["load_id"], ["loads.id"], name=op.f("fk_exception_resolutions_load_id_loads")),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"],
                                name=op.f("fk_exception_resolutions_proposal_id_proposals")),
        sa.UniqueConstraint("proposal_id", name=op.f("uq_exception_resolutions_proposal_id")),
        sa.CheckConstraint("action IN ('waived', 'referred')", name=op.f("ck_exception_resolutions_action")),
        sa.CheckConstraint("applied_by IN ('agent', 'manual')", name=op.f("ck_exception_resolutions_applied_by")),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_exception_resolutions_reason")),
    )
    op.create_index("ix_exception_resolutions_feed_rule", "exception_resolutions", ["feed_id", "rule_key", "pattern"])
    op.execute(RESOLUTION_GUARD)
    op.execute("CREATE TRIGGER trg_exception_resolutions_a_guard BEFORE INSERT ON exception_resolutions "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_exception_resolution()")
    op.execute("CREATE TRIGGER trg_exception_resolutions_append_only BEFORE UPDATE OR DELETE ON exception_resolutions "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_exception_resolutions_no_truncate BEFORE TRUNCATE ON exception_resolutions "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_truncate()")
    op.execute(WAIVER_OWNER)
    op.execute("CREATE TRIGGER trg_approvals_d_waiver_owner BEFORE INSERT ON approvals FOR EACH ROW "
               "EXECUTE FUNCTION amaze_guard_waiver_owner()")

    op.add_column("standard_records", sa.Column("waived", postgresql.ARRAY(sa.Text()),
                                                server_default=sa.text("'{}'::text[]"), nullable=False))
    op.create_check_constraint(op.f("ck_standard_records_flagged_or_waived"), "standard_records",
                               "NOT (flags && waived)")
    op.execute(WAIVED_RECORD)
    op.execute("CREATE TRIGGER trg_standard_records_c_waived BEFORE INSERT ON standard_records FOR EACH ROW "
               "WHEN (cardinality(NEW.waived) > 0) EXECUTE FUNCTION amaze_guard_waived_record()")

    op.add_column("load_certifications", sa.Column("waived_rows", sa.Integer(), server_default="0", nullable=False))
    op.add_column("load_certifications", sa.Column("waived_counts", postgresql.JSONB(),
                                                   server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.create_check_constraint(op.f("ck_load_certifications_waived"), "load_certifications",
                               "waived_rows >= 0 AND flagged_rows + waived_rows <= rows")
    op.execute(_certification_0034())

    op.add_column("loads", sa.Column("rerun_of_load_id", sa.Uuid(), nullable=True))
    op.add_column("loads", sa.Column("rerun_reason", sa.Text(), nullable=True))
    op.create_foreign_key(op.f("fk_loads_rerun_of_load_id_loads"), "loads", "loads", ["rerun_of_load_id"], ["id"])
    op.create_check_constraint(op.f("ck_loads_rerun_has_reason"), "loads",
                               "(rerun_of_load_id IS NULL AND rerun_reason IS NULL) "
                               "OR (rerun_of_load_id IS NOT NULL AND COALESCE(length(btrim(rerun_reason)) > 0, false))")
    op.execute(LOAD_RERUN)
    op.execute("CREATE TRIGGER trg_loads_c_rerun BEFORE INSERT OR UPDATE ON loads FOR EACH ROW "
               "EXECUTE FUNCTION amaze_guard_load_rerun()")

    op.execute(_delete_function_0034())


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM exception_resolutions)
               OR EXISTS (SELECT 1 FROM proposals WHERE kind = 'exception_fix')
               OR EXISTS (SELECT 1 FROM loads WHERE rerun_of_load_id IS NOT NULL)
               OR EXISTS (SELECT 1 FROM standard_records WHERE cardinality(waived) > 0)
               OR EXISTS (SELECT 1 FROM load_certifications WHERE waived_rows > 0) THEN
                RAISE EXCEPTION 'migration 0034 cannot be downgraded: there are exception fixes, resolutions, reruns '
                                'or waived records, which 0033 cannot hold';
            END IF;
        END $$;
        """)
    op.execute(_delete_function_0032())
    op.execute("DROP TRIGGER trg_loads_c_rerun ON loads")
    op.execute("DROP FUNCTION amaze_guard_load_rerun()")
    op.drop_constraint(op.f("ck_loads_rerun_has_reason"), "loads", type_="check")
    op.drop_constraint(op.f("fk_loads_rerun_of_load_id_loads"), "loads", type_="foreignkey")
    op.drop_column("loads", "rerun_reason")
    op.drop_column("loads", "rerun_of_load_id")
    op.execute(_certification_0019())
    op.drop_constraint(op.f("ck_load_certifications_waived"), "load_certifications", type_="check")
    op.drop_column("load_certifications", "waived_counts")
    op.drop_column("load_certifications", "waived_rows")
    op.execute("DROP TRIGGER trg_standard_records_c_waived ON standard_records")
    op.execute("DROP FUNCTION amaze_guard_waived_record()")
    op.drop_constraint(op.f("ck_standard_records_flagged_or_waived"), "standard_records", type_="check")
    op.drop_column("standard_records", "waived")
    op.execute("DROP TRIGGER trg_approvals_d_waiver_owner ON approvals")
    op.execute("DROP FUNCTION amaze_guard_waiver_owner()")
    op.drop_table("exception_resolutions")
    op.execute("DROP FUNCTION amaze_guard_exception_resolution()")
    op.drop_index("uq_proposals_one_pending_exception_fix", table_name="proposals")
    op.drop_constraint(op.f("ck_proposals_exception_fix"), "proposals", type_="check")
    op.drop_constraint(op.f("ck_proposals_manual_kinds"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_manual_kinds"), "proposals",
                               f"origin = 'agent' OR ({_in('kind', MANUAL_0029)} AND feed_id IS NOT NULL)")
    op.drop_constraint(op.f("ck_proposals_kind"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_kind"), "proposals", _in("kind", _kinds_0029()))
