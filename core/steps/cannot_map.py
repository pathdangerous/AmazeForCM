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

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.domain.onboarding import CannotMapColumn, OnboardingSession
from core.repositories.configuration import MappingRepository
from core.repositories.glossary_runs import GlossaryRunRepository
from core.repositories.onboarding import OnboardingRepository
from core.repositories.proposals import ProposalRepository
from core.steps.s06_map import excluded_columns


def recorded(session: Session, session_id) -> dict[str, str]:
    """{column: reason} of the mapping step's latest answer since the step was last entered."""
    mine = [e for e in OnboardingRepository(session).events(session_id) if e.step == "mapping"]
    entered = max((i for i, e in enumerate(mine) if e.event == "entered"), default=-1)
    answers = [e for e in mine[entered + 1:] if e.event == "agent_done"]
    return dict(answers[-1].detail.get("cannot_map") or {}) if answers else {}


def requested_columns(session: Session, feed_id: UUID) -> dict[str, str]:
    """{column: where its glossary request stands} for the feed's columns a person requested (a `column_requested` run)
    that are still neither mapped nor left out. drafting: the builder has not answered; no_proposal: it answered with
    nothing; pending: the glossary owner has not decided; approved: the element exists, its mapping is the steward's;
    rejected: the owner said no."""
    proposals = ProposalRepository(session)
    mapped = {m.source_field for m in MappingRepository(session).current_for_feed(feed_id)}
    left_out = excluded_columns(session, feed_id) | {
        p.payload["source_field"] for p in proposals.list_for_feed(feed_id, "mapping", "pending")
        if p.payload.get("excluded")}
    latest: dict[str, str] = {}
    for p in proposals.list_for_feed(feed_id, "glossary_element"):
        if p.status != "superseded":
            latest[p.payload["source_field"]] = p.status
    found: dict[str, str] = {}
    for run in GlossaryRunRepository(session).list_for_feed(feed_id):
        if run.reason != "column_requested":
            continue
        for column in run.columns:
            if column in mapped or column in left_out:
                continue
            found[column] = latest.get(column) or ("drafting" if run.status in ("queued", "running") else "no_proposal")
    return found


WAITING = ("drafting", "pending", "approved")  # a request that is still on its way (rejected, no_proposal: it is not)


class ColumnsNotLoaded(BaseModel):
    """Phase 7.22, plan D9: the feed's columns the loads leave out, by name: those a person ignored, and those waiting
    for a glossary element (requested, not mapped yet). Read only."""

    model_config = ConfigDict(frozen=True)

    ignored: list[str]
    waiting_for_glossary: list[str]


def columns_not_loaded(session: Session, feed_id: UUID) -> ColumnsNotLoaded:
    mapped = {m.source_field for m in MappingRepository(session).current_for_feed(feed_id)}
    waiting = sorted(c for c, status in requested_columns(session, feed_id).items() if status in WAITING)
    return ColumnsNotLoaded(ignored=sorted(excluded_columns(session, feed_id) - mapped), waiting_for_glossary=waiting)


def cannot_map_of(session: Session, sess: OnboardingSession) -> list[CannotMapColumn]:
    proposals = ProposalRepository(session)
    mapped = {m.source_field for m in MappingRepository(session).current_for_feed(sess.feed_id)}
    left_out = excluded_columns(session, sess.feed_id)
    requested = requested_columns(session, sess.feed_id)
    found = []
    for column, reason in recorded(session, sess.id).items():
        if column in mapped:
            continue
        pending = proposals.pending_for_column("mapping", sess.feed_id, column)
        if pending is not None and not pending.payload.get("excluded"):
            continue  # a mapping proposal for it is an ordinary item of the step
        if pending is not None or column in left_out:
            found.append(CannotMapColumn(column=column, reason=reason, state="ignored"))
        elif requested.get(column) in WAITING:
            found.append(CannotMapColumn(column=column, reason=reason, state="requested",
                                         glossary_status=requested[column]))
        else:  # not decided, or its request came to nothing: a person chooses again (Ignore)
            found.append(CannotMapColumn(column=column, reason=reason, state="undecided",
                                         glossary_status=requested.get(column)))
    return found
