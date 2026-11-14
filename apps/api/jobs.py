"""The daily fetch: one job, registered at every API start-up, that reads the feeds table when it runs.

Carried item 3 (phase 3 plan, assumptions 17 and 18). Nothing per feed is kept in the scheduler, so an
API restart loses nothing: the new start-up registers the same job, and the job finds every feed in the
database. The Azure Functions timer (phase 8) has the same shape.

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
from core.steps.s4_fetch import fetch_source
from ports.config import Settings
from ports.connectors import SourceConnector, get_connector
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
        )

    def run(self, today: date | None = None) -> dict[str, str]:
        """Fetch every feed with a source. Returns feed code -> outcome, or "error: ..." for that feed."""
        with self.session_factory() as session:
            feeds = [(f.id, f.code) for f in FeedRepository(session).list_with_source()]
        results: dict[str, str] = {}
        for feed_id, code in feeds:
            with self.session_factory() as session:
                notifier = get_notifier(self.settings, session=session) if self.settings else InAppNotifier(session)
                try:
                    result = fetch_source(
                        session, feed_id=feed_id, connector_factory=self.connector_factory,
                        blob_store=self.blob_store, notifier=notifier, backend_team=self.backend_team(),
                        today=today,
                    )
                    session.commit()
                    results[code] = result.outcome
                except Exception as exc:  # one feed must not stop the others
                    session.rollback()
                    log.exception("daily fetch of feed %s failed", code)
                    results[code] = f"error: {exc}"
        return results


def register_daily_fetch(scheduler: Scheduler, daily: DailyFetch) -> None:
    """Called at every start-up. The same id replaces any job with that id."""
    scheduler.schedule_daily(DAILY_FETCH_JOB_ID, daily.run, hour=DAILY_FETCH_HOUR)
