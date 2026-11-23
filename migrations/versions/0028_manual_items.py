"""manual onboarding items: a proposal made by a person has an author and no logged model call

Revision ID: 0028
Revises: 0027

Phase 7.11, T7 (docs/plans/phase-7.11-t7.md).

proposals
- `origin` ('agent' | 'manual', default 'agent') and `author`. `proposal_log_id` may now be NULL.
  ck_proposals_origin_matches_source: an agent proposal has a logged call and no author; a manual one has an author
  (not blank) and no logged call. A manual item is never given a made-up log row: no model was called (rule 4 logs
  model calls, and there was none).
  ck_proposals_manual_kinds: a person adds only glossary_element, mapping, transformation and rule proposals, and
  always to a feed. ck_proposals_manual_rule_back_tested: a manual rule carries its back-test (step 09's code ran it).
- The edit guard (0024) now also allows editing a review_summary, where only its `notes` may change (a go-live note),
  and an edit keeps origin and author. amaze_edit_proposal() copies both onto the new row.
- The mapping guard (0016): for a manual mapping only, the row's element must be the proposal's recommended element
  and must exist in the glossary, instead of one of the proposal's candidates. A registered feed, the next version and
  the column are still checked. An agent's mapping is unchanged.
- proposal_audit now LEFT JOINs the logged call (a manual proposal has none) and shows origin and author.
- onboarding_events may be `item_added`.
- amaze_delete_feed() (0027) skips a NULL proposal_log_id when it collects the feed's logged calls.

Function bodies are built from the 0016, 0024 and 0027 text with each replacement asserted, so a changed source line
stops the migration instead of being skipped. Downgrade stops while a manual proposal, a go-live note or an
`item_added` event exists. PostgreSQL 14 features only.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

HERE = Path(__file__).resolve().parent
EVENTS_0024 = ("entered", "agent_queued", "agent_started", "agent_done", "agent_failed", "pre_accepted", "edited",
               "approved", "rejected", "back", "stale", "done", "call_cap_reached")
EVENTS_0028 = (*EVENTS_0024, "item_added")
MANUAL_KINDS = ("glossary_element", "mapping", "transformation", "rule")


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, next(HERE.glob(f"{name}_*.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _swap(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"0028 expected exactly one: {old[:70]!r}"
    return text.replace(old, new)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _mapping_guard_0028(old: str) -> str:
    text = _swap(old, "    f_stage text;\nBEGIN\n", "    f_stage text;\n    p_origin text;\nBEGIN\n")
    return _swap(
        text,
        "    IF NOT COALESCE(jsonb_typeof(p_payload -> 'candidates') = 'array'\n"
        "                    AND (p_payload -> 'candidates') ? NEW.element_key, false) THEN\n"
        "        RAISE EXCEPTION 'mapping: % is not a candidate of proposal %', NEW.element_key, NEW.proposal_id\n"
        "            USING ERRCODE = 'check_violation';\n"
        "    END IF;\n",
        "    SELECT origin INTO p_origin FROM proposals WHERE id = NEW.proposal_id;\n"
        "    IF p_origin IS NOT DISTINCT FROM 'manual' THEN\n"
        "        IF (p_payload ->> 'recommended') IS DISTINCT FROM NEW.element_key THEN\n"
        "            RAISE EXCEPTION 'mapping: % is not the element proposal % chose by hand (%)', NEW.element_key,\n"
        "                NEW.proposal_id, COALESCE(p_payload ->> 'recommended', 'none') USING ERRCODE = 'check_violation';\n"
        "        END IF;\n"
        "        IF NOT EXISTS (SELECT 1 FROM glossary_elements WHERE key = NEW.element_key) THEN\n"
        "            RAISE EXCEPTION 'mapping: % is not a glossary element', NEW.element_key\n"
        "                USING ERRCODE = 'check_violation';\n"
        "        END IF;\n"
        "    ELSIF NOT COALESCE(jsonb_typeof(p_payload -> 'candidates') = 'array'\n"
        "                    AND (p_payload -> 'candidates') ? NEW.element_key, false) THEN\n"
        "        RAISE EXCEPTION 'mapping: % is not a candidate of proposal %', NEW.element_key, NEW.proposal_id\n"
        "            USING ERRCODE = 'check_violation';\n"
        "    END IF;\n")


def _edit_guard_0028(old: str) -> str:
    text = _swap(old, "IF o.kind NOT IN ('glossary_element', 'mapping', 'transformation', 'rule') THEN",
                 "IF o.kind NOT IN ('glossary_element', 'mapping', 'transformation', 'rule', 'review_summary') THEN")
    text = _swap(
        text,
        "    IF NEW.kind IS DISTINCT FROM o.kind THEN\n",
        "    IF NEW.origin IS DISTINCT FROM o.origin OR NEW.author IS DISTINCT FROM o.author THEN\n"
        "        RAISE EXCEPTION 'proposals: an edit keeps the origin and author of proposal %', o.id\n"
        "            USING ERRCODE = 'check_violation';\n"
        "    END IF;\n"
        "    IF NEW.kind IS DISTINCT FROM o.kind THEN\n")
    return _swap(
        text,
        "    IF o.kind = 'rule' AND (NEW.payload ->> 'rule_key')",
        "    IF o.kind = 'review_summary' AND ((NEW.payload - 'notes') IS DISTINCT FROM (o.payload - 'notes')\n"
        "                                      OR jsonb_typeof(COALESCE(NEW.payload -> 'notes', '[]'::jsonb))\n"
        "                                         IS DISTINCT FROM 'array') THEN\n"
        "        RAISE EXCEPTION 'proposals: an edit of review summary % changes only its notes', o.id\n"
        "            USING ERRCODE = 'check_violation';\n"
        "    END IF;\n"
        "    IF o.kind = 'rule' AND (NEW.payload ->> 'rule_key')")


def _edit_function_0028(old: str) -> str:
    return _swap(
        old,
        "edited_from, edited_by)\n    VALUES (o.proposal_log_id, o.kind, new_payload, o.autonomy, o.required_approver_roles, o.requirement_id,\n"
        "            o.feed_id, o.id, editor)",
        "edited_from, edited_by, origin, author)\n    VALUES (o.proposal_log_id, o.kind, new_payload, o.autonomy, o.required_approver_roles, o.requirement_id,\n"
        "            o.feed_id, o.id, editor, o.origin, o.author)")


AUDIT_VIEW_0028 = """
CREATE OR REPLACE VIEW proposal_audit AS
SELECT p.id AS proposal_id, p.kind, p.status, p.payload, p.autonomy,
       p.required_approver_roles, p.created_at AS proposed_at, p.decided_at,
       l.id AS proposal_log_id, l.agent_name, l.provider, l.model, l.model_version,
       l.prompt_id, l.prompt_version, l.input_ref, l.output, l.raw_ref, l.latency_ms,
       COALESCE(
           (SELECT jsonb_agg(jsonb_build_object(
                       'step', a.step, 'role', a.role, 'decided_by', a.decided_by,
                       'decision', a.decision, 'note', a.note, 'decided_at', a.created_at)
                   ORDER BY a.step)
              FROM approvals a WHERE a.proposal_id = p.id),
           '[]'::jsonb) AS decisions,
       p.origin, p.author
  FROM proposals p
  LEFT JOIN proposal_log l ON l.id = p.proposal_log_id
"""

AUDIT_VIEW_0002 = """
CREATE OR REPLACE VIEW proposal_audit AS
SELECT p.id AS proposal_id, p.kind, p.status, p.payload, p.autonomy,
       p.required_approver_roles, p.created_at AS proposed_at, p.decided_at,
       l.id AS proposal_log_id, l.agent_name, l.provider, l.model, l.model_version,
       l.prompt_id, l.prompt_version, l.input_ref, l.output, l.raw_ref, l.latency_ms,
       COALESCE(
           (SELECT jsonb_agg(jsonb_build_object(
                       'step', a.step, 'role', a.role, 'decided_by', a.decided_by,
                       'decision', a.decision, 'note', a.note, 'decided_at', a.created_at)
                   ORDER BY a.step)
              FROM approvals a WHERE a.proposal_id = p.id),
           '[]'::jsonb) AS decisions
  FROM proposals p
  JOIN proposal_log l ON l.id = p.proposal_log_id
"""

DELETE_NEEDLE = ("SELECT DISTINCT proposal_log_id, p_feed FROM proposals\n"
                 "         WHERE feed_id = p_feed AND id NOT IN (SELECT id FROM amaze_feed_kept)")
DELETE_PATCHED = ("SELECT DISTINCT proposal_log_id, p_feed FROM proposals\n"
                  "         WHERE feed_id = p_feed AND id NOT IN (SELECT id FROM amaze_feed_kept)\n"
                  "           AND proposal_log_id IS NOT NULL  -- a manual item has no logged call")


def _delete_function() -> str:
    """The 0027 text of amaze_delete_feed() (a migration runs offline too, so the text is built, not read back)."""
    return _module("0027")._delete_feed_function().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def upgrade() -> None:
    op.add_column("proposals", sa.Column("origin", sa.Text(), server_default="agent", nullable=False))
    op.add_column("proposals", sa.Column("author", sa.Text(), nullable=True))
    op.alter_column("proposals", "proposal_log_id", existing_type=sa.Uuid(), nullable=True)
    op.create_check_constraint(op.f("ck_proposals_origin"), "proposals", "origin IN ('agent', 'manual')")
    op.create_check_constraint(
        op.f("ck_proposals_origin_matches_source"), "proposals",
        "(origin = 'agent' AND proposal_log_id IS NOT NULL AND author IS NULL) "
        "OR (origin = 'manual' AND proposal_log_id IS NULL AND author IS NOT NULL AND length(btrim(author)) > 0)")
    op.create_check_constraint(
        op.f("ck_proposals_manual_kinds"), "proposals",
        f"origin = 'agent' OR ({_in('kind', MANUAL_KINDS)} AND feed_id IS NOT NULL)")
    op.create_check_constraint(
        op.f("ck_proposals_manual_rule_back_tested"), "proposals",
        "origin = 'agent' OR kind <> 'rule' OR COALESCE(jsonb_typeof(payload -> 'backtest') = 'object' "
        "AND (payload -> 'backtest' ->> 'status') IN ('ran', 'not_back_tested'), false)")

    old_0024, old_0016 = _module("0024"), _module("0016")
    op.execute(_edit_guard_0028(old_0024.PROPOSAL_EDIT_GUARD).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute(_edit_function_0028(old_0024.EDIT_FUNCTION).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute(_mapping_guard_0028(old_0016.MAPPING_MATCHES).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute(AUDIT_VIEW_0028)

    op.drop_constraint(op.f("ck_onboarding_events_event"), "onboarding_events", type_="check")
    op.create_check_constraint(op.f("ck_onboarding_events_event"), "onboarding_events", _in("event", EVENTS_0028))

    op.execute(_swap(_delete_function(), DELETE_NEEDLE, DELETE_PATCHED))


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM proposals WHERE origin = 'manual' OR (kind = 'review_summary' AND payload ? 'notes'))
               OR EXISTS (SELECT 1 FROM onboarding_events WHERE event = 'item_added') THEN
                RAISE EXCEPTION 'migration 0028 cannot be downgraded: there are manual items, go-live notes or '
                                'item_added events, which 0027 cannot hold';
            END IF;
        END $$;
        """)
    op.execute(_delete_function())
    op.drop_constraint(op.f("ck_onboarding_events_event"), "onboarding_events", type_="check")
    op.create_check_constraint(op.f("ck_onboarding_events_event"), "onboarding_events", _in("event", EVENTS_0024))

    old_0024, old_0016 = _module("0024"), _module("0016")
    op.execute(AUDIT_VIEW_0002.replace("CREATE OR REPLACE VIEW", "DROP VIEW IF EXISTS proposal_audit;\nCREATE VIEW", 1))
    op.execute(old_0016.MAPPING_MATCHES.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute(old_0024.EDIT_FUNCTION.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute(old_0024.PROPOSAL_EDIT_GUARD.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.drop_constraint(op.f("ck_proposals_manual_rule_back_tested"), "proposals", type_="check")
    op.drop_constraint(op.f("ck_proposals_manual_kinds"), "proposals", type_="check")
    op.drop_constraint(op.f("ck_proposals_origin_matches_source"), "proposals", type_="check")
    op.drop_constraint(op.f("ck_proposals_origin"), "proposals", type_="check")
    op.alter_column("proposals", "proposal_log_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("proposals", "author")
    op.drop_column("proposals", "origin")
