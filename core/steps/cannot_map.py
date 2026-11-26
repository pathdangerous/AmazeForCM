"""Cannot map: no glossary element fits (phase 7.22, plan docs/plans/phase-7.22.md, decision D2). Read only.

The MappingAgent maps a column to an existing glossary element or it does not. A column it could not map is the typed
outcome `no_element_back_to_05` (step 06); in the onboarding the mapping job writes it into its `agent_done` event
(`cannot_map: {column: reason}`) and sends nothing back. This module derives, from that event and the tables, what each
such column's state is. It never writes; the decisions (Ignore, Request glossary element) are in core/steps/onboarding.py.

state, per column:
  ignored    a pending or approved mapping proposal with `excluded` (or a selected exclude_column action) leaves it out;
  requested  a glossary request exists for it (T3);
  undecided  none of these: a person chooses.
A column that got a mapping, or a pending mapping proposal that maps it, is no longer cannot-map.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.domain.onboarding import CannotMapColumn, OnboardingSession
from core.repositories.configuration import MappingRepository
from core.repositories.onboarding import OnboardingRepository
from core.repositories.proposals import ProposalRepository
from core.steps.s06_map import excluded_columns


def recorded(session: Session, session_id) -> dict[str, str]:
    """{column: reason} of the mapping step's latest answer since the step was last entered."""
    mine = [e for e in OnboardingRepository(session).events(session_id) if e.step == "mapping"]
    entered = max((i for i, e in enumerate(mine) if e.event == "entered"), default=-1)
    answers = [e for e in mine[entered + 1:] if e.event == "agent_done"]
    return dict(answers[-1].detail.get("cannot_map") or {}) if answers else {}


def cannot_map_of(session: Session, sess: OnboardingSession) -> list[CannotMapColumn]:
    proposals = ProposalRepository(session)
    mapped = {m.source_field for m in MappingRepository(session).current_for_feed(sess.feed_id)}
    left_out = excluded_columns(session, sess.feed_id)
    found = []
    for column, reason in recorded(session, sess.id).items():
        if column in mapped:
            continue
        pending = proposals.pending_for_column("mapping", sess.feed_id, column)
        if pending is not None and not pending.payload.get("excluded"):
            continue  # a mapping proposal for it is an ordinary item of the step
        state = "ignored" if pending is not None or column in left_out else "undecided"
        found.append(CannotMapColumn(column=column, reason=reason, state=state))
    return found
