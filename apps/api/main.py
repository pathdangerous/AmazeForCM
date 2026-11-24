"""Amaze for CM PoC API. Run: .venv\\Scripts\\python -m uvicorn apps.api.main:app --port 8000

Routers: health and dev identity (phase 0), Amaze for the backend team (phase 1), consumption for the
requester (phase 2).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from apps.api.deps import get_app_settings, get_db_engine
from apps.api.jobs import (
    DailyFetch,
    GlossaryRuns,
    OnboardingJobs,
    ReportJob,
    kick_glossary_runs,
    kick_onboarding_jobs,
    register_daily_fetch,
    register_report_job,
)
from apps.api.routers import (amaze, audit, consumer, dev_identity, exceptions, health, notifications, onboarding,
                              overview, reports, uploads)
from core.repositories.settings import FourEyesRepository
from ports.config import get_settings
from ports.identity import get_identity
from ports.llm.base import LLMRefused, LLMUnavailable
from ports.scheduler import get_scheduler


log = logging.getLogger(__name__)


def default_four_eyes_sync(app: FastAPI) -> None:
    """Phase 7.8, D2: write REQUIRE_FOUR_EYES into the database (a new version only when it changed), because
    the database guards read the row, not the environment. No database configured: nothing to write (health
    reports it). Any other failure is logged and stops start-up."""
    settings = app.dependency_overrides.get(get_app_settings, get_app_settings)()
    engine = app.dependency_overrides.get(get_db_engine, get_db_engine)()
    if engine is None:
        log.warning("REQUIRE_FOUR_EYES not written: no database is configured")
        return
    try:
        with Session(bind=engine) as session:
            wrote = FourEyesRepository(session).set(settings.require_four_eyes, set_by="api-startup",
                                                    reason="REQUIRE_FOUR_EYES at start-up")
            session.commit()
        if wrote:
            log.info("REQUIRE_FOUR_EYES=%s written to amaze_settings", settings.require_four_eyes)
    except Exception:
        log.exception("REQUIRE_FOUR_EYES could not be written to amaze_settings; start-up stops")
        raise


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # an invalid selector stops start-up here with a ConfigError
    app.state.identity = get_identity(settings)
    # Tests set app.state.four_eyes_sync (running_app puts a no-op there, so no API test commits a settings row).
    (getattr(app.state, "four_eyes_sync", None) or default_four_eyes_sync)(app)
    scheduler = get_scheduler(settings)
    app.state.scheduler = scheduler
    # A stub scheduler is not started; /api/health reports it as unavailable instead.
    runs_scheduler = not getattr(scheduler, "is_stub", False)
    # Phase 5: the glossary runs worker, whatever the scheduler, so a kick after a commit always has one to
    # name (with a stub scheduler the kick fails, is logged and swallowed, and the answer stays 200: the row is
    # taken at the next kick or start-up; phase 6 plan, assumption 30).
    # Tests set app.state.glossary_runs (running_app puts a do-nothing one there).
    if getattr(app.state, "glossary_runs", None) is None:
        app.state.glossary_runs = GlossaryRuns.from_settings(settings, app.state.identity)
    # Phase 7.8, task 5.4: the onboarding jobs worker, the same way (running_app puts a do-nothing one there).
    if getattr(app.state, "onboarding_jobs", None) is None:
        app.state.onboarding_jobs = OnboardingJobs.from_settings(settings, app.state.identity)
    if runs_scheduler:
        # The daily fetch is registered at every start: it reads the feeds table when it runs, so a
        # restart loses nothing (phase 3 plan, assumption 17). Tests set app.state.daily_fetch.
        daily = getattr(app.state, "daily_fetch", None) or DailyFetch.from_settings(settings, app.state.identity)
        if daily.kick_glossary is None:  # an additive change wakes the glossary worker after its commit
            daily.kick_glossary = lambda: kick_glossary_runs(scheduler, app.state.glossary_runs)
        if settings.daily_job_enabled:  # DAILY_JOB_ENABLED=false: none (the e2e API; plan risk 6)
            register_daily_fetch(scheduler, daily)
            register_report_job(scheduler, ReportJob.from_settings(settings, app.state.identity))  # phase 7.15
        scheduler.start()
        # Kicked at every start so runs left by the last process are taken.
        kick_glossary_runs(scheduler, app.state.glossary_runs)
        kick_onboarding_jobs(scheduler, app.state.onboarding_jobs)  # resume after a restart (task 5.4)
    try:
        yield
    finally:
        if runs_scheduler:
            scheduler.shutdown()


app = FastAPI(title="Amaze for CM PoC API", version="0.0.0", lifespan=lifespan)


@app.exception_handler(NotImplementedError)
async def adapter_not_implemented(request: Request, exc: NotImplementedError) -> JSONResponse:
    """A request reached an Azure stub adapter: report 503 (service unavailable), not a 500 crash."""
    return JSONResponse(status_code=503, content={"detail": str(exc) or "Not implemented"})


@app.exception_handler(LLMUnavailable)
async def model_unavailable(request: Request, exc: LLMUnavailable) -> JSONResponse:
    """The model provider did not answer (timeout, rate limit, provider error): an expected case, 503 with
    its reason. The step wrote nothing; the requester can try again (independent review, fix 4)."""
    return JSONResponse(status_code=503, content={"detail": "The model did not answer. Try again later.",
                                                  "reason": exc.reason})


@app.exception_handler(LLMRefused)
async def model_refused(request: Request, exc: LLMRefused) -> JSONResponse:
    """The model or its content filter refused this input (phase 7.8): 422 with its reason. Nothing was written;
    asking again with the same input will not help."""
    return JSONResponse(status_code=422, content={"detail": "The model refused to answer this input.",
                                                  "reason": exc.reason})


app.include_router(health.router)
app.include_router(dev_identity.router)
app.include_router(amaze.router)
app.include_router(overview.router)
app.include_router(exceptions.router)
app.include_router(reports.router)
app.include_router(audit.router)
app.include_router(uploads.router)
app.include_router(uploads.feed_files_router)
app.include_router(onboarding.router)
app.include_router(consumer.router)
app.include_router(notifications.router)
