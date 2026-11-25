"""glossary import: a proposal kind for one element row of an uploaded file, decided by the glossary owner

Revision ID: 0038
Revises: 0037

Phase 7.19, T1 (docs/plans/phase-7.19.md, section 4). A glossary_import proposal is one row of an uploaded glossary file:
made by a person (origin manual, author = the uploader, no logged call: no model was called), no feed, no requirement,
decided by the glossary_owner alone. Approving it writes a new element (version 1 of a new key) or the next version of an
existing key, never an edit in place (rule 7). Nothing is loosened except for this kind.

New versions (each built from the text of the migration that last defined it, with every replacement asserted):
- ck_proposals_kind (0034): + 'glossary_import'.
- ck_proposals_manual_kinds (0034): + OR (kind = 'glossary_import' AND feed_id IS NULL). Every other manual kind still has a feed.
- amaze_require_approved_proposal() (0005): the kind test also accepts glossary_import, for the glossary_elements table only.
- amaze_require_glossary_option() (0025): a glossary_import branch. The row equals its proposal's element; version 1 of a
  key that does not exist ('new'), or the next version of a key whose latest version is the proposal's base_version
  ('update'); no alias that is new to the element is held by another element.
New: ck_proposals_glossary_import_shape, amaze_guard_glossary_import_proposal() (the upload exists, the author is the
uploader, a new key is new, an update key exists), uq_proposals_one_pending_glossary_import_per_key, and the append-only
glossary_uploads table (who, when, file, SHA-256, counts by label, every row with its label).
Not changed: ck_proposals_names_a_column (a glossary_element or mapping proposal without a feed is still refused), the mapping
guard, the proposal change guard, the edit guard (glossary_import is not an editable kind) and proposal_audit.

Downgrade stops, naming 0038, while a glossary_import proposal or an upload exists. PostgreSQL 14 features only.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

HERE = Path(__file__).resolve().parent


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"m{name}", next(HERE.glob(f"{name}_*.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _swap(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"0038 expected exactly one: {old[:70]!r}"
    return text.replace(old, new)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _kinds_0034() -> tuple[str, ...]:
    return (*_module("0034")._kinds_0029(), "exception_fix")


MANUAL_0034 = (*_module("0034").MANUAL_0029, "exception_fix")
MANUAL_KINDS_0034 = f"origin = 'agent' OR ({_in('kind', MANUAL_0034)} AND feed_id IS NOT NULL)"
MANUAL_KINDS_0038 = MANUAL_KINDS_0034 + " OR (kind = 'glossary_import' AND feed_id IS NULL)"

# Spelt out so that a missing or JSON-null field is false, never NULL (a CHECK that is NULL passes).
SHAPE = (
    "kind <> 'glossary_import' OR (origin = 'manual' AND feed_id IS NULL AND requirement_id IS NULL "
    "AND autonomy = 'A1' AND required_approver_roles = ARRAY['glossary_owner']::text[] "
    "AND COALESCE(jsonb_typeof(payload -> 'element') = 'object', false) "
    "AND COALESCE(jsonb_typeof(payload -> 'upload_id') = 'string', false) "
    "AND COALESCE((payload ->> 'label') IN ('new', 'update'), false) "
    "AND COALESCE(length(btrim(payload ->> 'key')) > 0, false) "
    "AND COALESCE((payload -> 'element' ->> 'key') = (payload ->> 'key'), false))")


def _approved_proposal_0038(old: str) -> str:
    return _swap(
        old, "    IF p_kind <> wanted THEN\n",
        "    IF p_kind <> wanted AND NOT (TG_TABLE_NAME = 'glossary_elements' AND wanted = 'glossary_element'\n"
        "                                 AND p_kind = 'glossary_import') THEN\n")


IMPORT_BRANCH = """    SELECT * INTO latest FROM glossary_elements WHERE key = NEW.key ORDER BY version DESC LIMIT 1;
    IF p_kind = 'glossary_import' THEN
        el := p_payload -> 'element';
        IF NOT ((el ->> 'key') IS NOT DISTINCT FROM NEW.key
                AND (el ->> 'name') IS NOT DISTINCT FROM NEW.name
                AND (el ->> 'domain') IS NOT DISTINCT FROM NEW.domain
                AND (el ->> 'data_type') IS NOT DISTINCT FROM NEW.data_type
                AND (el ->> 'definition') IS NOT DISTINCT FROM NEW.definition
                AND COALESCE(el -> 'aliases', '[]'::jsonb) IS NOT DISTINCT FROM to_jsonb(NEW.aliases)
                AND (el ->> 'unit') IS NOT DISTINCT FROM NEW.unit
                AND ((p_payload ->> 'label' = 'new' AND latest.id IS NULL AND NEW.version = 1)
                     OR (p_payload ->> 'label' = 'update' AND latest.id IS NOT NULL
                         AND NEW.version = latest.version + 1
                         AND (p_payload ->> 'base_version') IS NOT DISTINCT FROM latest.version::text))) THEN
            RAISE EXCEPTION 'glossary element % version % does not match the row of import proposal %', NEW.key,
                NEW.version, NEW.proposal_id USING ERRCODE = 'check_violation';
        END IF;
        SELECT o.key, x INTO held_by, held_alias
          FROM (SELECT DISTINCT ON (key) key, aliases FROM glossary_elements WHERE key <> NEW.key
                 ORDER BY key, version DESC) o, unnest(o.aliases) AS x
         WHERE x = ANY(NEW.aliases) LIMIT 1;
        IF held_by IS NOT NULL THEN
            RAISE EXCEPTION 'glossary element %: the alias % is already held by %', NEW.key, held_alias, held_by
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
"""


def _option_0038(old: str) -> str:
    text = _swap(old, "    el jsonb;\nBEGIN\n", "    el jsonb;\n    p_kind text;\n    held_by text;\n    held_alias text;\nBEGIN\n")
    text = _swap(text, "    SELECT payload INTO p_payload FROM proposals WHERE id = NEW.proposal_id;\n",
                 "    SELECT payload, kind INTO p_payload, p_kind FROM proposals WHERE id = NEW.proposal_id;\n")
    return _swap(text, "    SELECT * INTO latest FROM glossary_elements WHERE key = NEW.key ORDER BY version DESC LIMIT 1;\n",
                 IMPORT_BRANCH)


PROPOSAL_GUARD = """
CREATE FUNCTION amaze_guard_glossary_import_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    uploader text;
    found_upload boolean;
    key_exists boolean;
BEGIN
    IF NEW.kind <> 'glossary_import' OR NEW.origin <> 'manual' THEN
        RETURN NEW;  -- another kind; a glossary_import that is not a person's is refused by its shape check
    END IF;
    SELECT uploaded_by, true INTO uploader, found_upload FROM glossary_uploads
     WHERE id::text = NEW.payload ->> 'upload_id';
    IF found_upload IS NOT TRUE THEN
        RAISE EXCEPTION 'glossary_import proposal: no upload %', COALESCE(NEW.payload ->> 'upload_id', 'named')
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.author IS DISTINCT FROM uploader THEN
        RAISE EXCEPTION 'glossary_import proposal: author % is not who uploaded the file (uploaded by %)', NEW.author,
            uploader USING ERRCODE = 'check_violation';
    END IF;
    key_exists := EXISTS (SELECT 1 FROM glossary_elements WHERE key = NEW.payload ->> 'key');
    IF NEW.payload ->> 'label' = 'new' AND key_exists THEN
        RAISE EXCEPTION 'glossary_import proposal: % is already a glossary element; that is an update, not new',
            NEW.payload ->> 'key' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.payload ->> 'label' = 'update' AND NOT key_exists THEN
        RAISE EXCEPTION 'glossary_import proposal: % is not a glossary element; that is new, not an update',
            NEW.payload ->> 'key' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "glossary_uploads",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("uploaded_by", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.Text(), nullable=False),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rows", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_glossary_uploads")),
        sa.CheckConstraint("length(btrim(uploaded_by)) > 0", name=op.f("ck_glossary_uploads_uploaded_by")),
        sa.CheckConstraint("length(btrim(file_name)) > 0", name=op.f("ck_glossary_uploads_file_name")),
        sa.CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_glossary_uploads_file_sha256")),
        sa.CheckConstraint("jsonb_typeof(counts) = 'object' AND jsonb_typeof(rows) = 'array'",
                           name=op.f("ck_glossary_uploads_counts_and_rows")),
    )
    op.execute("CREATE TRIGGER trg_glossary_uploads_append_only BEFORE UPDATE OR DELETE ON glossary_uploads "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_glossary_uploads_no_truncate BEFORE TRUNCATE ON glossary_uploads "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_truncate()")

    op.drop_constraint(op.f("ck_proposals_kind"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_kind"), "proposals", _in("kind", (*_kinds_0034(), "glossary_import")))
    op.drop_constraint(op.f("ck_proposals_manual_kinds"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_manual_kinds"), "proposals", MANUAL_KINDS_0038)
    op.create_check_constraint(op.f("ck_proposals_glossary_import_shape"), "proposals", SHAPE)
    op.create_index("uq_proposals_one_pending_glossary_import_per_key", "proposals", [sa.text("(payload ->> 'key')")],
                    unique=True, postgresql_where=sa.text("status = 'pending' AND kind = 'glossary_import'"))
    op.execute(PROPOSAL_GUARD)
    op.execute("CREATE TRIGGER trg_proposals_c_glossary_import BEFORE INSERT ON proposals "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_glossary_import_proposal()")

    op.execute(_approved_proposal_0038(_module("0005").STRICT))
    op.execute(_option_0038(_module("0025").GLOSSARY_OPTION_0025))


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM proposals WHERE kind = 'glossary_import')
               OR EXISTS (SELECT 1 FROM glossary_uploads) THEN
                RAISE EXCEPTION 'migration 0038 cannot be downgraded: there are glossary uploads or glossary_import '
                                'proposals, which 0037 cannot hold';
            END IF;
        END $$;
        """)
    op.execute(_module("0025").GLOSSARY_OPTION_0025)
    op.execute(_module("0005").STRICT)
    op.execute("DROP TRIGGER trg_proposals_c_glossary_import ON proposals")
    op.execute("DROP FUNCTION amaze_guard_glossary_import_proposal()")
    op.drop_index("uq_proposals_one_pending_glossary_import_per_key", table_name="proposals")
    op.drop_constraint(op.f("ck_proposals_glossary_import_shape"), "proposals", type_="check")
    op.drop_constraint(op.f("ck_proposals_manual_kinds"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_manual_kinds"), "proposals", MANUAL_KINDS_0034)
    op.drop_constraint(op.f("ck_proposals_kind"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_kind"), "proposals", _in("kind", _kinds_0034()))
    op.drop_table("glossary_uploads")
