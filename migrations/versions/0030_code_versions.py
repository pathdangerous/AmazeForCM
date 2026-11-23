"""code_versions: the generated code of a feed, one version per go-live approval, pinned by the go-live

Revision ID: 0030
Revises: 0029

Phase 7.12, T3 (0029 is T1's: the profile action kind). Rule 7: configuration is versioned, never edited in place; the
code generated from it is too.

- code_versions: feed, version (1, 2, 3 ...), sha256, files (jsonb: path -> text), the mapping, rule and transformation ids
  it was generated from, the test load and its result, approved_by. Append-only (UPDATE, DELETE and TRUNCATE refused; a
  DELETE only while amaze_delete_feed() deletes the row's own feed, as 0027 allows for every guarded table).
  trg_code_versions_guard: the version is the next one for the feed and sha256 is the SHA-256 of the files (the text
  `path US content` of each file in path order, joined by RS: US = 0x1f, RS = 0x1e), recomputed here, so a version whose
  hash is not its files' is refused.
- feed_go_lives.code_version_id and trg_feed_go_lives_c_code_version: every NEW go-live pins a code version of its own
  feed, approved by the same owner, from the same test load and the same mapping, rule and transformation ids.
  Earlier go-lives keep NULL.
- amaze_delete_feed() (0027, patched by 0028) also deletes the feed's code versions, after its go-lives.

Downgrade stops, naming 0030, while a code version exists. PostgreSQL 14 features only (sha256() is built in from 11).
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

HERE = Path(__file__).resolve().parent


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"m{name}", next(HERE.glob(f"{name}_*.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _delete_function_0028() -> str:
    """amaze_delete_feed() as 0028 left it: 0027's text with the NULL-log-id patch."""
    m27, m28 = _module("0027"), _module("0028")
    return m28._swap(m27._delete_feed_function().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
                     m28.DELETE_NEEDLE, m28.DELETE_PATCHED)


def _delete_function_0030() -> str:
    m27 = _module("0027")
    after = m27._delete_block("feed_go_lives", "feed_id = p_feed", "feed_go_lives")
    added = m27._delete_block("code_versions", "feed_id = p_feed", "code_versions")
    text = _delete_function_0028()
    assert text.count(after) == 1, "0030 expected exactly one feed_go_lives delete block"
    return text.replace(after, after + added)


CODE_VERSION_GUARD = r"""
CREATE FUNCTION amaze_guard_code_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    last_version integer;
    expected text;
BEGIN
    IF jsonb_typeof(NEW.files) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'code_versions: files must be a JSON object of path to text' USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(version) INTO last_version FROM code_versions WHERE feed_id = NEW.feed_id;
    IF NEW.version IS DISTINCT FROM COALESCE(last_version, 0) + 1 THEN
        RAISE EXCEPTION 'code_versions: version % is not the next one (%)', NEW.version, COALESCE(last_version, 0) + 1
            USING ERRCODE = 'check_violation';
    END IF;
    expected := encode(sha256(convert_to(
        COALESCE((SELECT string_agg(k || E'\x1f' || v, E'\x1e' ORDER BY k COLLATE "C")
                    FROM jsonb_each_text(NEW.files) AS t(k, v)), ''), 'UTF8')), 'hex');
    IF NEW.sha256 IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'code_versions: sha256 % is not the hash of the files (%)', NEW.sha256, expected
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

GO_LIVE_PINS_CODE = """
CREATE FUNCTION amaze_go_live_pins_code_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    c code_versions%ROWTYPE;
BEGIN
    IF NEW.code_version_id IS NULL THEN
        RAISE EXCEPTION 'go-live: a go-live pins the code version generated from its configuration (no code version)'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO c FROM code_versions WHERE id = NEW.code_version_id;
    IF NOT FOUND OR c.feed_id IS DISTINCT FROM NEW.feed_id THEN
        RAISE EXCEPTION 'go-live: code version % is not a code version of feed %', NEW.code_version_id, NEW.feed_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF c.approved_by IS DISTINCT FROM NEW.approved_by OR c.test_load_id IS DISTINCT FROM NEW.test_load_id THEN
        RAISE EXCEPTION 'go-live: code version % was not approved by % from test load %', c.version, NEW.approved_by,
            NEW.test_load_id USING ERRCODE = 'check_violation';
    END IF;
    IF ARRAY(SELECT u FROM unnest(c.mapping_ids) AS m(u) ORDER BY 1)
           IS DISTINCT FROM ARRAY(SELECT u FROM unnest(NEW.mapping_ids) AS m(u) ORDER BY 1)
       OR ARRAY(SELECT u FROM unnest(c.rule_ids) AS m(u) ORDER BY 1)
           IS DISTINCT FROM ARRAY(SELECT u FROM unnest(NEW.rule_ids) AS m(u) ORDER BY 1)
       OR ARRAY(SELECT u FROM unnest(c.transformation_ids) AS m(u) ORDER BY 1)
           IS DISTINCT FROM ARRAY(SELECT u FROM unnest(NEW.transformation_ids) AS m(u) ORDER BY 1) THEN
        RAISE EXCEPTION 'go-live: code version % was generated from other mappings, rules or transformations',
            c.version USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "code_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("mapping_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("rule_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("transformation_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("test_load_id", sa.Uuid(), nullable=True),
        sa.Column("test_load_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_code_versions")),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], name=op.f("fk_code_versions_feed_id_feeds")),
        sa.ForeignKeyConstraint(["test_load_id"], ["loads.id"], name=op.f("fk_code_versions_test_load_id_loads")),
        sa.UniqueConstraint("feed_id", "version", name=op.f("uq_code_versions_feed_id_version")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_code_versions_version")),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_code_versions_sha256")),
        sa.CheckConstraint("length(btrim(approved_by)) > 0", name=op.f("ck_code_versions_approved_by")),
    )
    op.execute(CODE_VERSION_GUARD)
    op.execute("CREATE TRIGGER trg_code_versions_a_guard BEFORE INSERT ON code_versions FOR EACH ROW "
               "EXECUTE FUNCTION amaze_guard_code_version()")
    op.execute("CREATE TRIGGER trg_code_versions_append_only BEFORE UPDATE OR DELETE ON code_versions "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_code_versions_no_truncate BEFORE TRUNCATE ON code_versions "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_truncate()")

    op.add_column("feed_go_lives", sa.Column("code_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f("fk_feed_go_lives_code_version_id_code_versions"), "feed_go_lives", "code_versions",
                          ["code_version_id"], ["id"])
    op.execute(GO_LIVE_PINS_CODE)
    op.execute("CREATE TRIGGER trg_feed_go_lives_c_code_version BEFORE INSERT ON feed_go_lives FOR EACH ROW "
               "EXECUTE FUNCTION amaze_go_live_pins_code_version()")
    op.execute(_delete_function_0030())


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM code_versions) THEN
                RAISE EXCEPTION 'migration 0030 cannot be downgraded: there are code versions, which 0029 cannot hold';
            END IF;
        END $$;
        """)
    op.execute(_delete_function_0028())
    op.execute("DROP TRIGGER trg_feed_go_lives_c_code_version ON feed_go_lives")
    op.execute("DROP FUNCTION amaze_go_live_pins_code_version()")
    op.drop_constraint(op.f("fk_feed_go_lives_code_version_id_code_versions"), "feed_go_lives", type_="foreignkey")
    op.drop_column("feed_go_lives", "code_version_id")
    op.drop_table("code_versions")
    op.execute("DROP FUNCTION amaze_guard_code_version()")
