"""amaze_erase_profiles(): the recorded erase of a feed's old profiles

Revision ID: 0036
Revises: 0035

Phase 7.16, T1 (plan: docs/plans/phase-7.16.md, 2.3). An exception to rules 1 and 7 (CLAUDE.md), as 7.9's feed delete is:
a profile is a measurement of a file, not configuration; it is deleted only through amaze_erase_profiles(), and the erase is
recorded.

- profile_erasures (new, append-only, no FK: it outlives the feed): feed, who, why, the cutoff, the ids and versions erased,
  the profiles kept with the reason. The database checks that the counts match the arrays.
- amaze_profile_keep_reason(profile): why a profile may not be erased, or NULL. In order: `latest` (the feed's highest
  version), `registration` (a registration's sign-off is of it), `signoff`, `glossary_run`, `load_arrival` (a load's
  arrival_profile_id), `code_version` (a code version's files name its id), `report` (a report's numbers name its id). The
  feed page reads the same function, so the screen and the delete cannot disagree.
- amaze_erase_profiles(feed, before, reason, by): needs a reason and a person; locks the feed row (FOR NO KEY UPDATE, the
  first lock of the lock order); takes the feed's profiles made before `before`; keeps those with a keep reason; deletes the
  rest and writes one profile_erasures row; returns it as jsonb. When nothing would be erased it raises (no_data_found,
  "nothing to erase") and writes no record.
- amaze_may_delete() (0027) has one more branch: a feed_profiles row may be deleted while its id is in
  pg_temp.amaze_erasable with no keep reason, a temp table the function makes, fills and drops. Nothing else deletes a
  profile (amaze_delete_feed() still may, for its own feed: its branch is unchanged). TRUNCATE is still refused.

Downgrade stops, naming 0036, while an erase is recorded (its profiles are gone, so the record could not be rebuilt).
PostgreSQL 14 features only: plpgsql, jsonb_build_object, array_agg with ORDER BY, to_regclass, temp tables.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

HERE = Path(__file__).resolve().parent


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"m{name}", next(HERE.glob(f"{name}_*.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _swap(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"0036 expected exactly one: {old[:70]!r}"
    return text.replace(old, new)


def _may_delete_0027() -> str:
    return _module("0027").MAY_DELETE.replace("CREATE FUNCTION amaze_may_delete", "CREATE OR REPLACE FUNCTION amaze_may_delete", 1)


def _may_delete_0036() -> str:
    body = _swap(_may_delete_0027(), "    owner_feed uuid;\nBEGIN\n    BEGIN\n",
                 "    owner_feed uuid;\n    erasable boolean;\nBEGIN\n"
                 "    IF tbl = 'feed_profiles' AND to_regclass('pg_temp.amaze_erasable') IS NOT NULL THEN\n"
                 "        -- amaze_erase_profiles() decided this row may go: it has no keep reason (0036)\n"
                 "        EXECUTE 'SELECT EXISTS (SELECT 1 FROM pg_temp.amaze_erasable WHERE id = $1 AND reason IS NULL)'\n"
                 "            INTO erasable USING (old ->> 'id')::uuid;\n"
                 "        IF erasable THEN\n"
                 "            RETURN true;\n"
                 "        END IF;\n"
                 "    END IF;\n"
                 "    BEGIN\n")
    return body


KEEP_REASON = """
CREATE FUNCTION amaze_profile_keep_reason(p_profile uuid) RETURNS text LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN p.id = (SELECT l.id FROM feed_profiles l WHERE l.feed_id = p.feed_id ORDER BY l.version DESC LIMIT 1)
            THEN 'latest'
        WHEN EXISTS (SELECT 1 FROM feed_signoffs s JOIN feed_registrations r ON r.signoff_id = s.id WHERE s.profile_id = p.id)
            THEN 'registration'
        WHEN EXISTS (SELECT 1 FROM feed_signoffs s WHERE s.profile_id = p.id) THEN 'signoff'
        WHEN EXISTS (SELECT 1 FROM glossary_runs g WHERE g.profile_id = p.id) THEN 'glossary_run'
        WHEN EXISTS (SELECT 1 FROM loads l WHERE l.arrival_profile_id = p.id) THEN 'load_arrival'
        WHEN EXISTS (SELECT 1 FROM code_versions c WHERE c.files::text LIKE '%' || p.id::text || '%') THEN 'code_version'
        WHEN EXISTS (SELECT 1 FROM reports r WHERE r.numbers::text LIKE '%' || p.id::text || '%') THEN 'report'
        ELSE NULL
    END
    FROM feed_profiles p WHERE p.id = p_profile
$$;
"""

ERASE = """
CREATE FUNCTION amaze_erase_profiles(p_feed uuid, p_before timestamptz, p_reason text, p_by text) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
    f feeds%ROWTYPE;
    erased uuid[];
    erased_versions integer[];
    kept jsonb;
    erasure uuid;
BEGIN
    IF p_reason IS NULL OR btrim(p_reason) = '' THEN
        RAISE EXCEPTION 'an erase needs a reason' USING ERRCODE = 'check_violation';
    END IF;
    IF p_by IS NULL OR btrim(p_by) = '' THEN
        RAISE EXCEPTION 'an erase needs the person who erases' USING ERRCODE = 'check_violation';
    END IF;
    IF p_before IS NULL THEN
        RAISE EXCEPTION 'an erase needs the time before which profiles go' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO f FROM feeds WHERE id = p_feed FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'feed % does not exist', p_feed USING ERRCODE = 'no_data_found';
    END IF;
    DROP TABLE IF EXISTS pg_temp.amaze_erasable;
    CREATE TEMP TABLE amaze_erasable (id uuid PRIMARY KEY, version integer NOT NULL, reason text) ON COMMIT DROP;
    INSERT INTO amaze_erasable (id, version, reason)
        SELECT id, version, amaze_profile_keep_reason(id) FROM feed_profiles
         WHERE feed_id = p_feed AND created_at < p_before;
    IF NOT EXISTS (SELECT 1 FROM amaze_erasable WHERE reason IS NULL) THEN
        RAISE EXCEPTION 'nothing to erase for feed %: every profile before % is kept, or there is none', f.code, p_before
            USING ERRCODE = 'no_data_found';
    END IF;
    SELECT array_agg(id ORDER BY version), array_agg(version ORDER BY version) INTO erased, erased_versions
      FROM amaze_erasable WHERE reason IS NULL;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('id', id, 'version', version, 'reason', reason) ORDER BY version), '[]'::jsonb)
      INTO kept FROM amaze_erasable WHERE reason IS NOT NULL;
    DELETE FROM feed_profiles WHERE id IN (SELECT id FROM amaze_erasable WHERE reason IS NULL);
    INSERT INTO profile_erasures (feed_id, feed_code, before, "by", reason, erased_count, erased_ids, erased_versions, kept,
                                  kept_count)
        VALUES (p_feed, f.code, p_before, p_by, btrim(p_reason), cardinality(erased), erased, erased_versions, kept,
                jsonb_array_length(kept))
        RETURNING id INTO erasure;
    DROP TABLE amaze_erasable;  -- the window closes before the function returns
    RETURN jsonb_build_object('erasure_id', erasure, 'feed_id', p_feed, 'feed_code', f.code, 'erased_count', cardinality(erased),
                              'erased_ids', to_jsonb(erased), 'erased_versions', to_jsonb(erased_versions), 'kept', kept);
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "profile_erasures",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("feed_code", sa.Text(), nullable=False),
        sa.Column("before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("by", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("erased_count", sa.Integer(), nullable=False),
        sa.Column("erased_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("erased_versions", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("kept", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("kept_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_erasures")),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_profile_erasures_reason")),
        sa.CheckConstraint("length(btrim(\"by\")) > 0", name=op.f("ck_profile_erasures_by")),
        sa.CheckConstraint("erased_count >= 1 AND erased_count = cardinality(erased_ids) "
                           "AND erased_count = cardinality(erased_versions)", name=op.f("ck_profile_erasures_erased_count_matches")),
        sa.CheckConstraint("jsonb_typeof(kept) = 'array' AND kept_count = jsonb_array_length(kept)",
                           name=op.f("ck_profile_erasures_kept_count_matches")),
    )
    op.create_index("ix_profile_erasures_feed_id", "profile_erasures", ["feed_id"])
    op.execute("CREATE TRIGGER trg_profile_erasures_append_only BEFORE UPDATE OR DELETE ON profile_erasures "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_profile_erasures_no_truncate BEFORE TRUNCATE ON profile_erasures "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_truncate()")
    op.execute(KEEP_REASON)
    op.execute(_may_delete_0036())
    op.execute(ERASE)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM profile_erasures) THEN
                RAISE EXCEPTION 'migration 0036 cannot be downgraded: an erase is recorded, and its profiles are gone';
            END IF;
        END $$;
        """)
    op.execute("DROP FUNCTION amaze_erase_profiles(uuid, timestamptz, text, text)")
    op.execute("DROP FUNCTION amaze_profile_keep_reason(uuid)")
    op.execute(_may_delete_0027())
    op.drop_table("profile_erasures")
