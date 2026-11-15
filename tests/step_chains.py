"""Read, end the transaction, then call and write: one step on one test session (independent review, fix 5).

The API and the daily job run each agent-backed step, and the fetch, as two halves: a read-only half
(*_input, fetch_plan), then the end of that transaction, then the call with no connection held, then the
locked write. These chains do the same on one session, for step tests. They lived in core/steps until
the independent review; they are test helpers only, so no production code can call a model or a
connector inside an open transaction.

Ending the read is session.commit(): on the tests' db_session (savepoint mode) it releases the savepoint
and the test's work is still rolled back at the end. Agent.run() refuses a session still in a transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from core.steps.s1_scope import RunsAgent, record_scope, scope_input
from core.steps.s2_sources import record_sources, sources_input
from core.steps.s4_fetch import Fetched, SourceDown, fetch_file, fetch_plan, record_fetch
from core.steps.s17_discover import Assistance, assist_input, record_assist
from ports.connectors.base import SourceConnector
from ports.connectors.catalogue import SourceCatalogue
from ports.notify.base import Notifier
from ports.storage.base import BlobStore


def end_read(session: Session) -> None:
    session.commit()


def propose_scope(session: Session, *, requirement_id: UUID, agent: RunsAgent, catalogue: SourceCatalogue,
                  note: str | None = None, again: bool = False, notifier: Notifier | None = None,
                  backend_team: Sequence[str] = ()):
    agent_input = scope_input(session, requirement_id=requirement_id, catalogue=catalogue, note=note, again=again)
    end_read(session)
    return record_scope(session, agent=agent, agent_input=agent_input, again=again, notifier=notifier,
                        backend_team=backend_team)


def list_sources(session: Session, *, requirement_id: UUID, agent: RunsAgent, catalogue: SourceCatalogue,
                 notifier: Notifier, backend_team: Sequence[str]):
    agent_input = sources_input(session, requirement_id=requirement_id, catalogue=catalogue)
    end_read(session)
    return record_sources(session, requirement_id=requirement_id, agent=agent, agent_input=agent_input,
                          catalogue=catalogue, notifier=notifier, backend_team=backend_team)


def assist(session: Session, *, query: str, agent: RunsAgent, user_id: str, roles: Sequence[str],
           display_name: Callable[[str], str]) -> Assistance:
    agent_input = assist_input(session, query=query)
    end_read(session)
    return record_assist(session, agent=agent, agent_input=agent_input, user_id=user_id, roles=roles,
                         display_name=display_name)


def profile_feed(session: Session, *, feed_id: UUID, catalogue: SourceCatalogue, blob_store: BlobStore,
                 profiled_by: str):
    """Step 02: read the plan, end the transaction, run DuckDB with no session, then lock and write."""
    from core.steps.s02_profile import profile_plan, record_profile, run_profile

    plan = profile_plan(session, feed_id=feed_id, catalogue=catalogue)
    end_read(session)
    return record_profile(session, run_profile(plan, blob_store=blob_store), profiled_by=profiled_by)


def fetch_source(session: Session, *, feed_id: UUID, connector_factory: Callable[[str], SourceConnector],
                 blob_store: BlobStore, notifier: Notifier, backend_team: Sequence[str],
                 today: date | None = None, release_after_days: int | None = None,
                 catalogue: SourceCatalogue | None = None) -> Fetched | SourceDown:
    plan = fetch_plan(session, feed_id=feed_id, today=today)
    end_read(session)
    attempt = fetch_file(plan, connector_factory=connector_factory, blob_store=blob_store)
    return record_fetch(session, attempt, notifier=notifier, backend_team=backend_team,
                        release_after_days=release_after_days, catalogue=catalogue)
