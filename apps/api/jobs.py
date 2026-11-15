"""The daily fetch: one job, registered at every API start-up, that reads the feeds table when it runs.

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

from core.repositories.feeds import FeedRepository
from core.repositories.requirements import RequirementRepository
from core.steps.s3_pick import close_if_nothing_to_pick
from core.steps.s4_fetch import fetch_file, fetch_plan, record_fetch
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
        Returns feed code -> outcome, or "error: ..." for that feed."""
        catalogue = self.catalogue_loader() if self.catalogue_loader else None
        with self.session_factory() as session:
            feeds = [(f.id, f.code) for f in FeedRepository(session).list_to_fetch()]
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
