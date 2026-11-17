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


def run_glossary(session: Session, *, agent, notifier: Notifier, owners: Sequence[str], lease_seconds: int = 300):
    """Step 05 as the worker runs it (apps/api/jobs.py): claim and commit, read and end the read, then the
    call and the locked write. None when no run waits."""
    from core.steps.s05_glossary import builder_input, claim_run, record_run

    run = claim_run(session, lease_seconds=lease_seconds)
    end_read(session)
    if run is None:
        return None
    plan = builder_input(session, run)
    end_read(session)
    return record_run(session, plan=plan, agent=agent, notifier=notifier, owners=owners)


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


def summarise(session: Session, *, feed_id: UUID, agent: RunsAgent):
    """The ProfilerAgent's summary of the latest profile: read, end the transaction, call, lock and write."""
    from core.steps.s02_profile import record_summary, summary_input

    agent_input = summary_input(session, feed_id=feed_id)
    end_read(session)
    return record_summary(session, agent=agent, agent_input=agent_input)


def propose_rules(session: Session, *, feed_id: UUID, agent: RunsAgent, catalogue: SourceCatalogue,
                  connector_factory: Callable[[str], SourceConnector], rule_timeout_seconds: float = 5):
    """Step 09 as the API runs it: read the input, end the transaction, then the call (the back-test runs right
    after it, outside any transaction) and the locked write."""
    from core.steps.s09_rules import backtest_for, record_rules, rules_input

    agent_input = rules_input(session, feed_id=feed_id, catalogue=catalogue)
    end_read(session)
    return record_rules(session, agent=agent, agent_input=agent_input,
                        backtest=backtest_for(agent_input, connector_factory=connector_factory,
                                              rule_timeout_seconds=rule_timeout_seconds))


def prepare_go_live(session: Session, *, blob_store: BlobStore, agent: RunsAgent, feed_id: UUID, notifier: Notifier,
                    backend_team: Sequence[str], owner_is_data_owner: Callable[[str], bool],
                    rule_timeout_seconds: float = 5):
    """Step 11 prepare as the API runs it: the checks, a test load through the engine (plan, end the read, execute
    with no session, locked write), then a TestFailedHeld, or the summary (the agent's call with no connection held)."""
    from core.engine.run import execute_load, plan_load, record_load
    from core.steps.s11_go_live import after_test_load, begin_test_load, go_live_plan, record_summary

    plan = go_live_plan(session, feed_id=feed_id, owner_is_data_owner=owner_is_data_owner, notifier=notifier,
                        backend_team=backend_team)
    load = begin_test_load(session, plan)
    end_read(session)
    engine_plan = plan_load(session, load.id)
    end_read(session)
    outcome = execute_load(engine_plan, blob_store=blob_store, rule_timeout_seconds=rule_timeout_seconds)
    load_run = record_load(session, engine_plan, outcome)
    end_read(session)
    after = after_test_load(session, plan=plan, load_run=load_run, notifier=notifier, backend_team=backend_team)
    end_read(session)
    if not hasattr(after, "feed_id"):  # a TestFailedHeld
        return after
    return record_summary(session, agent=agent, agent_input=after)


def run_engine(session: Session, blob_store: BlobStore, feed_id: UUID, *, file_ref: str, purpose: str,
               go_live_id: UUID | None = None, fetch_id: UUID | None = None, rule_timeout_seconds: float = 5):
    """Step 13 as the API runs it: a received load, then plan (read), end the transaction, execute with no
    session (BlobStore.get() and the DuckDB sandbox), then the locked write of the stages and records."""
    from core.engine.run import execute_load, plan_load, record_load
    from core.repositories.loads import LoadRepository

    load = LoadRepository(session).create(feed_id=feed_id, file_ref=file_ref, purpose=purpose,
                                          go_live_id=go_live_id, fetch_id=fetch_id)
    end_read(session)
    plan = plan_load(session, load.id)
    end_read(session)
    outcome = execute_load(plan, blob_store=blob_store, rule_timeout_seconds=rule_timeout_seconds)
    return record_load(session, plan, outcome)


def fetch_source(session: Session, *, feed_id: UUID, connector_factory: Callable[[str], SourceConnector],
                 blob_store: BlobStore, notifier: Notifier, backend_team: Sequence[str],
                 today: date | None = None, release_after_days: int | None = None,
                 catalogue: SourceCatalogue | None = None) -> Fetched | SourceDown:
    plan = fetch_plan(session, feed_id=feed_id, today=today)
    end_read(session)
    attempt = fetch_file(plan, connector_factory=connector_factory, blob_store=blob_store)
    return record_fetch(session, attempt, notifier=notifier, backend_team=backend_team,
                        release_after_days=release_after_days, catalogue=catalogue)
