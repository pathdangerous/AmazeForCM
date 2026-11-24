"""the chain of drafts: proposal_bases, approval after the bases, onboarding_sessions.run_mode, chain events

Revision ID: 0032
Revises: 0031

Phase 7.13 (plan: docs/plans/phase-7.13.md, T0 "Triggers and constraints that need a new version"). One click runs
every onboarding agent ahead, each on the pending drafts before it; a person approves top to bottom. Rule 1 is
unchanged: a draft is a pending proposal, and nothing becomes configuration before its approval.

- proposal_bases (new, append-only): a draft and one upstream draft it was built on, with the feed of both (so 0027's
  amaze_may_delete() lets amaze_delete_feed() delete the row: it reads feed_id). trg_proposal_bases_a_guard: same feed,
  the base an earlier kind (glossary_element < mapping < transformation < rule), the draft an agent's (a person's
  item is built on approved configuration only). UPDATE, DELETE and TRUNCATE refused, as for every append-only table.
- trg_approvals_c_bases_approved (new, beside 0024's trg_approvals_not_superseded): an `approved` decision on a draft
  is refused while any of its bases is not approved (pending, rejected, or superseded by an edit).
- onboarding_sessions.run_mode (new): 'step' (the default, as before) or 'chain'. amaze_guard_onboarding_session()
  (0024, as 0027 left it) gets run_mode among the columns that move, and only step -> chain.
- ck_onboarding_events_event: 0028's names plus chain_started, chain_done, chain_stopped, draft_stale.
- amaze_delete_feed() (0027, patched by 0028 and 0030) also deletes the feed's proposal_bases, before its approvals.

Downgrade stops, naming 0032, while a base, a chain session or a chain event exists. PostgreSQL 14 features only.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
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
    assert text.count(old) == 1, f"0032 expected exactly one: {old[:70]!r}"
    return text.replace(old, new)


CHAIN_EVENTS = ("chain_started", "chain_done", "chain_stopped", "draft_stale")

BASE_GUARD = """
CREATE FUNCTION amaze_guard_proposal_base() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p proposals%ROWTYPE;
    b proposals%ROWTYPE;
    kinds text[] := ARRAY['glossary_element', 'mapping', 'transformation', 'rule'];
BEGIN
    SELECT * INTO p FROM proposals WHERE id = NEW.proposal_id;
    SELECT * INTO b FROM proposals WHERE id = NEW.base_id;
    IF p.feed_id IS DISTINCT FROM NEW.feed_id OR b.feed_id IS DISTINCT FROM NEW.feed_id THEN
        RAISE EXCEPTION 'proposal_bases: a draft and its base are of the same feed (%), not % and %', NEW.feed_id,
            COALESCE(p.feed_id::text, 'none'), COALESCE(b.feed_id::text, 'none') USING ERRCODE = 'check_violation';
    END IF;
    IF p.origin IS DISTINCT FROM 'agent' THEN
        RAISE EXCEPTION 'proposal_bases: only an agent draft is built on other drafts; proposal % is %', p.id, p.origin
            USING ERRCODE = 'check_violation';
    END IF;
    IF COALESCE(array_position(kinds, b.kind), 99) >= COALESCE(array_position(kinds, p.kind), 0) THEN
        RAISE EXCEPTION 'proposal_bases: a base is an earlier kind (glossary_element < mapping < transformation < rule); '
            '% is not earlier than %', b.kind, p.kind USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

BASES_APPROVED = """
CREATE FUNCTION amaze_require_bases_approved() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    open_base uuid;
    open_status text;
BEGIN
    IF NEW.decision IS DISTINCT FROM 'approved' THEN
        RETURN NEW;
    END IF;
    SELECT b.id, b.status INTO open_base, open_status
      FROM proposal_bases pb JOIN proposals b ON b.id = pb.base_id
     WHERE pb.proposal_id = NEW.proposal_id AND b.status IS DISTINCT FROM 'approved'
     LIMIT 1;
    IF open_base IS NOT NULL THEN
        RAISE EXCEPTION 'approval: proposal % is built on draft %, which is % and not approved: approve top to bottom',
            NEW.proposal_id, open_base, open_status USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def _session_guard_0027() -> str:
    return _module("0027").ONBOARDING_SESSION_NEW


def _session_guard_0032() -> str:
    text = _swap(_session_guard_0027(), "'updated_at', 'model_calls'];", "'updated_at', 'model_calls', 'run_mode'];")
    return _swap(text, "    IF NEW.model_calls < OLD.model_calls THEN\n", """    IF OLD.run_mode = 'chain' AND NEW.run_mode IS DISTINCT FROM 'chain' THEN
        RAISE EXCEPTION 'onboarding_sessions: run_mode of session % moves only from step to chain', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.model_calls < OLD.model_calls THEN
""")


def _delete_function_0030() -> str:
    return _module("0030")._delete_function_0030()


def _delete_function_0032() -> str:
    block = _module("0027")._delete_block("proposal_bases", "feed_id = p_feed", "proposal_bases")
    return _swap(_delete_function_0030(), "    DELETE FROM approvals WHERE", block + "    DELETE FROM approvals WHERE")


def upgrade() -> None:
    op.create_table(
        "proposal_bases",
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("base_id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("proposal_id", "base_id", name=op.f("pk_proposal_bases")),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], name=op.f("fk_proposal_bases_proposal_id_proposals")),
        sa.ForeignKeyConstraint(["base_id"], ["proposals.id"], name=op.f("fk_proposal_bases_base_id_proposals")),
        sa.CheckConstraint("proposal_id <> base_id", name=op.f("ck_proposal_bases_not_itself")),
    )
    op.execute(BASE_GUARD)
    op.execute("CREATE TRIGGER trg_proposal_bases_a_guard BEFORE INSERT ON proposal_bases FOR EACH ROW "
               "EXECUTE FUNCTION amaze_guard_proposal_base()")
    op.execute("CREATE TRIGGER trg_proposal_bases_append_only BEFORE UPDATE OR DELETE ON proposal_bases "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_proposal_bases_no_truncate BEFORE TRUNCATE ON proposal_bases "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_truncate()")
    op.execute(BASES_APPROVED)
    op.execute("CREATE TRIGGER trg_approvals_c_bases_approved BEFORE INSERT ON approvals FOR EACH ROW "
               "EXECUTE FUNCTION amaze_require_bases_approved()")

    op.add_column("onboarding_sessions", sa.Column("run_mode", sa.Text(), server_default="step", nullable=False))
    op.create_check_constraint(op.f("ck_onboarding_sessions_run_mode"), "onboarding_sessions",
                               "run_mode IN ('step', 'chain')")
    op.execute(_session_guard_0032())

    op.drop_constraint(op.f("ck_onboarding_events_event"), "onboarding_events", type_="check")
    op.create_check_constraint(op.f("ck_onboarding_events_event"), "onboarding_events",
                               _in("event", (*_module("0028").EVENTS_0028, *CHAIN_EVENTS)))
    op.execute(_delete_function_0032())


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM proposal_bases)
               OR EXISTS (SELECT 1 FROM onboarding_sessions WHERE run_mode = 'chain')
               OR EXISTS (SELECT 1 FROM onboarding_events WHERE {_in('event', CHAIN_EVENTS)}) THEN
                RAISE EXCEPTION 'migration 0032 cannot be downgraded: there are draft bases, chain sessions or chain '
                                'events, which 0031 cannot hold';
            END IF;
        END $$;
        """)
    op.execute(_delete_function_0030())
    op.drop_constraint(op.f("ck_onboarding_events_event"), "onboarding_events", type_="check")
    op.create_check_constraint(op.f("ck_onboarding_events_event"), "onboarding_events",
                               _in("event", _module("0028").EVENTS_0028))
    op.execute(_session_guard_0027())
    op.drop_constraint(op.f("ck_onboarding_sessions_run_mode"), "onboarding_sessions", type_="check")
    op.drop_column("onboarding_sessions", "run_mode")
    op.execute("DROP TRIGGER trg_approvals_c_bases_approved ON approvals")
    op.execute("DROP FUNCTION amaze_require_bases_approved()")
    op.drop_table("proposal_bases")
    op.execute("DROP FUNCTION amaze_guard_proposal_base()")
