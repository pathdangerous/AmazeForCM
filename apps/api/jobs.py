"""The daily fetch: one job, registered at every API start-up, that reads the feeds table when it runs.
Phase 5 adds GlossaryRuns: step 05's worker, started by Scheduler.run_soon() after a commit that queued
a run and at every start-up (the runs are rows in glossary_runs, so a restart loses nothing).

Carried item 3 (phase 3 plan, assumptions 17 and 18). Nothing per feed is kept in the scheduler, so an
API restart loses nothing: the new start-up registers the same job, and the job finds every feed in the
database. The Azure Functions timer (phase 8) has the same shape.

After the fetches, a requirement waiting on listed sources that have all left the catalogue becomes
no_source_found (independent review, fix 7).

Each feed is fetched in its own session and transaction. An unexpected error in one feed is rolled
back and reported; the other feeds are still fetched. source_down is an outcome, not an error.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from core.repositories.errors import RunSuperseded
from core.repositories.feeds import FeedRepository
from core.repositories.glossary_runs import GlossaryRunRepository
from core.repositories.requirements import RequirementRepository
from core.steps.s3_pick import close_if_nothing_to_pick
from core.steps.s4_fetch import fetch_file, fetch_plan, record_fetch
from core.steps.s05_glossary import builder_input, claim_run, fail_run, record_run
from ports.config import Settings
from ports.connectors import SourceConnector, get_connector
from ports.connectors.catalogue import SourceCatalogue
from ports.identity import Identity
from ports.notify import get_notifier
from ports.notify.inapp import InAppNotifier
from ports.scheduler import Scheduler
from ports.storage import BlobStore, get_blob_store

DAILY_FETCH_JOB_ID = "daily-source-fetch"
DAILY_FETCH_HOUR = 6  # UTC

log = logging.getLogger(__name__)


@dataclass
class DailyFetch:
    """What the job needs. The API builds it from the settings; tests build their own."""

    session_factory: Callable[[], AbstractContextManager[Session]]
    blob_store: BlobStore
    connector_factory: Callable[[str], SourceConnector]
    backend_team: Callable[[], Sequence[str]]
    settings: Settings | None = None  # selects the notifier; None means in-app
    # 3b: release after N failed days (SOURCE_DOWN_RELEASE_DAYS). The catalogue is loaded at the start of
    # each run, so an edited catalogue applies from the next run. None means no release rule.
    catalogue_loader: Callable[[], SourceCatalogue] | None = None
    release_after_days: int = 3

    @classmethod
    def from_settings(cls, settings: Settings, identity: Identity) -> DailyFetch:
        def sessions() -> AbstractContextManager[Session]:
            from ports.db import get_sessionmaker  # resolved when the job runs, not at start-up

            return get_sessionmaker()()

        return cls(
            session_factory=sessions,
            blob_store=get_blob_store(settings),
            connector_factory=lambda name: get_connector(name, settings),
            backend_team=lambda: [u.id for u in identity.list_users() if "backend_team" in u.roles],
            settings=settings,
            catalogue_loader=lambda: SourceCatalogue.load(settings.source_catalogue, settings.allowed_source_hosts()),
            release_after_days=settings.source_down_release_days,
        )

    def run(self, today: date | None = None) -> dict[str, str]:
        """Fetch every registered feed, and every candidate feed a requirement currently selects (Q4).
        Returns feed code -> outcome, or "error: ..." for that feed.

        A catalogue that fails to load (phase 4 plan, assumption 36; note 4d): registered feeds are still
        fetched, with no N-day release and no closing of source lists; candidate feeds wait for the next
        run with a good catalogue; the backend team is told once per run, with the error."""
        catalogue, broken = None, None
        if self.catalogue_loader:
            try:
                catalogue = self.catalogue_loader()
            except Exception as exc:  # a bad file must not stop the registered feeds
                log.exception("the source catalogue could not be read")
                broken = exc
        with self.session_factory() as session:
            feeds = [(f.id, f.code) for f in FeedRepository(session).list_to_fetch()
                     if broken is None or f.stage == "registered"]
        if broken is not None:
            self._tell_catalogue_broken(broken)
        results: dict[str, str] = {}
        for feed_id, code in feeds:
            with self.session_factory() as session:
                notifier = get_notifier(self.settings, session=session) if self.settings else InAppNotifier(session)
                try:
                    # Read, end the transaction, fetch with no connection held, lock the feed, write.
                    plan = fetch_plan(session, feed_id=feed_id, today=today)
                    session.rollback()
                    attempt = fetch_file(plan, connector_factory=self.connector_factory, blob_store=self.blob_store)
                    result = record_fetch(
                        session, attempt, notifier=notifier, backend_team=self.backend_team(), catalogue=catalogue,
                        release_after_days=self.release_after_days if catalogue else None,
                    )
                    session.commit()
                    results[code] = result.outcome
                except Exception as exc:  # one feed must not stop the others
                    session.rollback()
                    log.exception("daily fetch of feed %s failed", code)
                    results[code] = f"error: {exc}"
        if catalogue is not None:
            self._close_lists_with_nothing_to_pick(catalogue)
        return results

    def _tell_catalogue_broken(self, error: Exception) -> None:
        with self.session_factory() as session:
            notifier = get_notifier(self.settings, session=session) if self.settings else InAppNotifier(session)
            try:
                for user_id in self.backend_team():
                    notifier.notify(recipient=user_id, subject="Source catalogue could not be read",
                                    body=f"The source catalogue could not be read: {error}. Registered feeds were "
                                         "fetched; candidate feeds wait for the next run with a good catalogue.")
                session.commit()
            except Exception:
                session.rollback()
                log.exception("telling the backend team about the catalogue failed")

    def _close_lists_with_nothing_to_pick(self, catalogue: SourceCatalogue) -> None:
        """Every awaiting_source_choice requirement whose listed sources all left the catalogue becomes
        no_source_found (independent review, fix 7). Each in its own transaction; one failure does not
        stop the others."""
        with self.session_factory() as session:
            waiting = RequirementRepository(session).ids_in_status("awaiting_source_choice")
            session.rollback()
        for requirement_id in waiting:
            with self.session_factory() as session:
                notifier = get_notifier(self.settings, session=session) if self.settings else InAppNotifier(session)
                try:
                    if close_if_nothing_to_pick(session, requirement_id=requirement_id, catalogue=catalogue,
                                                notifier=notifier, backend_team=self.backend_team()):
                        log.info("requirement %s: no listed source is left in the catalogue", requirement_id)
                    session.commit()
                except Exception:
                    session.rollback()
                    log.exception("closing the source list of requirement %s failed", requirement_id)


def register_daily_fetch(scheduler: Scheduler, daily: DailyFetch) -> None:
    """Called at every start-up. The same id replaces any job with that id."""
    scheduler.schedule_daily(DAILY_FETCH_JOB_ID, daily.run, hour=DAILY_FETCH_HOUR)


# ---------------------------------------------------------------- phase 5: the glossary runs worker

GLOSSARY_RUNS_JOB_ID = "glossary-runs"
LEASE_GRACE_SECONDS = 1.0  # the later kick comes this long after the lease it waits for


@dataclass
class GlossaryRuns:
    """Step 05 in the background (phase 5 plan, assumptions 10 to 12 and 19). drain() claims and runs until
    no run waits. Each run: claim and commit; read and end the read; the model call with no connection held;
    then the locked write and commit. Safe to run twice at once: a claim takes a run once (SKIP LOCKED), and
    a late answer is discarded (RunSuperseded). The API builds it from the settings; tests build their own."""

    session_factory: Callable[[], AbstractContextManager[Session]]
    agent_factory: Callable[[], object]  # a GlossaryBuilderAgent
    owners: Callable[[], Sequence[str]]
    lease_seconds: int = 300
    settings: Settings | None = None  # selects the notifier; None means in-app

    @classmethod
    def from_settings(cls, settings: Settings, identity: Identity) -> GlossaryRuns:
        def sessions() -> AbstractContextManager[Session]:
            from ports.db import get_sessionmaker

            return get_sessionmaker()()

        def agent():
            from agents.glossary_builder import GlossaryBuilderAgent
            from ports.llm import get_llm_provider

            return GlossaryBuilderAgent(provider=get_llm_provider(settings), blob_store=get_blob_store(settings))

        return cls(session_factory=sessions, agent_factory=agent,
                   owners=lambda: [u.id for u in identity.list_users() if "glossary_owner" in u.roles],
                   lease_seconds=settings.glossary_run_lease_seconds, settings=settings)

    def drain(self) -> dict:
        """Run id -> done, failed or superseded, for every run this call took."""
        results = {}
        while True:
            with self.session_factory() as session:
                run = claim_run(session, lease_seconds=self.lease_seconds)
                session.commit()
            if run is None:
                return results
            results[run.id] = self._run_one(run)

    def lease_wait(self) -> float | None:
        """Seconds until the earliest live lease of a running run passes; None when there is none. Such a
        run may belong to a worker that died with the last process: nothing claims it before its lease."""
        with self.session_factory() as session:
            wait = GlossaryRunRepository(session).seconds_to_live_lease()
            session.rollback()
        return wait

    def _run_one(self, run) -> str:
        with self.session_factory() as session:
            notifier = get_notifier(self.settings, session=session) if self.settings else InAppNotifier(session)
            try:
                plan = builder_input(session, run)
                session.rollback()  # end the read: no connection is held during the model call
                record_run(session, plan=plan, agent=self.agent_factory(), notifier=notifier, owners=self.owners())
                session.commit()
                return "done"
            except RunSuperseded:  # another worker took it after the lease; the discard row is committed
                session.rollback()
                return "superseded"
            except Exception as exc:
                session.rollback()
                log.exception("glossary run %s failed", run.id)
                error = f"{type(exc).__name__}: {exc}"
        with self.session_factory() as session:
            try:
                fail_run(session, run=run, error=error)
                session.commit()
                return "failed"
            except RunSuperseded:
                session.rollback()
                return "superseded"


def kick_glossary_runs(scheduler: Scheduler, runs: GlossaryRuns, *, delay_seconds: float = 0.0) -> None:
    """After a commit that queued a run, and at every start-up (rows left by the last process). When the
    drain leaves a running run whose lease has not passed (its worker may have died with the last process,
    and nothing claims it before its lease), one more kick is scheduled just after the earliest such lease
    (independent review, finding 2). The scheduler keeps one waiting kick, the newest."""

    def work() -> None:
        runs.drain()
        wait = runs.lease_wait()
        if wait is not None:
            kick_glossary_runs(scheduler, runs, delay_seconds=wait + LEASE_GRACE_SECONDS)

    scheduler.run_soon(GLOSSARY_RUNS_JOB_ID, work, delay_seconds=delay_seconds)
