"""column requests: a glossary run for requested columns only, and the event that records the request

Revision ID: 0040
Revises: 0039

Phase 7.22, T3 (plan: docs/plans/phase-7.22.md, section 6). The onboarding maps to the existing glossary first; a column no
element fits is Ignored or Requested by a person. A request queues a glossary run for that column only.

- ck_glossary_runs_reason (0016, widened by 0019): registered, requested, sent_back, structure_change, and the new
  column_requested.
- ck_onboarding_events_event (0024, widened by 0028 and 0032): the 0032 names plus glossary_requested.

Two CHECK constraints take a new version; no trigger or function does (the plan, section 6, says why for each one it
looked at). Downgrade stops, naming 0040, while a column_requested run or a glossary_requested event exists.
PostgreSQL 14 features only.
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

HERE = Path(__file__).resolve().parent

REASONS_0019 = ("registered", "requested", "sent_back", "structure_change")
REASONS_0040 = (*REASONS_0019, "column_requested")


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"m{name}", next(HERE.glob(f"{name}_*.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


EVENTS_0032 = (*_module("0028").EVENTS_0028, *_module("0032").CHAIN_EVENTS)
EVENTS_0040 = (*EVENTS_0032, "glossary_requested")


def upgrade() -> None:
    op.drop_constraint(op.f("ck_glossary_runs_reason"), "glossary_runs", type_="check")
    op.create_check_constraint(op.f("ck_glossary_runs_reason"), "glossary_runs", _in("reason", REASONS_0040))
    op.drop_constraint(op.f("ck_onboarding_events_event"), "onboarding_events", type_="check")
    op.create_check_constraint(op.f("ck_onboarding_events_event"), "onboarding_events", _in("event", EVENTS_0040))


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM glossary_runs WHERE reason = 'column_requested')
               OR EXISTS (SELECT 1 FROM onboarding_events WHERE event = 'glossary_requested') THEN
                RAISE EXCEPTION 'migration 0040 cannot be downgraded: there are column_requested glossary runs or '
                                'glossary_requested events, which 0039 cannot hold';
            END IF;
        END $$;
        """)
    op.drop_constraint(op.f("ck_onboarding_events_event"), "onboarding_events", type_="check")
    op.create_check_constraint(op.f("ck_onboarding_events_event"), "onboarding_events", _in("event", EVENTS_0032))
    op.drop_constraint(op.f("ck_glossary_runs_reason"), "glossary_runs", type_="check")
    op.create_check_constraint(op.f("ck_glossary_runs_reason"), "glossary_runs", _in("reason", REASONS_0019))
