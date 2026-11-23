"""proposals.kind accepts profile_action, and a person may add one

Revision ID: 0029
Revises: 0028

Phase 7.12, T1. The ProfilerAgent proposes ACTIONS beside its summary (parse a date, turn placeholders into nulls, decide a
key policy, leave a column out, add a rule), one `profile_action` proposal each on the summary's logged call; a person
selects (approves) or rejects each. A person may add one (T7): a manual proposal, no logged call.

- ck_proposals_kind gains 'profile_action' (the list is built from 0023's, which is the last to change it).
- ck_proposals_manual_kinds (0028) gains 'profile_action'.
- ck_proposals_names_a_feed_for_actions: a profile_action has a feed.

Downgrade stops, naming 0029, while a profile_action proposal exists. PostgreSQL 14 features only.
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

HERE = Path(__file__).resolve().parent
MANUAL_0028 = ("glossary_element", "mapping", "transformation", "rule")
MANUAL_0029 = (*MANUAL_0028, "profile_action")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _kinds_0023() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("m0023", next(HERE.glob("0023_*.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.KINDS_0023)


def _manual_kinds(values: tuple[str, ...]) -> str:
    return f"origin = 'agent' OR ({_in('kind', values)} AND feed_id IS NOT NULL)"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_proposals_kind"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_kind"), "proposals", _in("kind", (*_kinds_0023(), "profile_action")))
    op.drop_constraint(op.f("ck_proposals_manual_kinds"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_manual_kinds"), "proposals", _manual_kinds(MANUAL_0029))
    op.create_check_constraint(op.f("ck_proposals_names_a_feed_for_actions"), "proposals",
                               "kind <> 'profile_action' OR feed_id IS NOT NULL")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM proposals WHERE kind = 'profile_action') THEN
                RAISE EXCEPTION 'migration 0029 cannot be downgraded: there are profile_action proposals, which '
                                '0028 cannot hold';
            END IF;
        END $$;
        """)
    op.drop_constraint(op.f("ck_proposals_names_a_feed_for_actions"), "proposals", type_="check")
    op.drop_constraint(op.f("ck_proposals_manual_kinds"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_manual_kinds"), "proposals", _manual_kinds(MANUAL_0028))
    op.drop_constraint(op.f("ck_proposals_kind"), "proposals", type_="check")
    op.create_check_constraint(op.f("ck_proposals_kind"), "proposals", _in("kind", _kinds_0023()))
