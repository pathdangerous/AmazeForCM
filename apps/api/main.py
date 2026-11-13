"""Amaze for CM PoC API. Run: .venv\\Scripts\\python -m uvicorn apps.api.main:app --port 8000

Routers: health and dev identity (phase 0), Amaze for the backend team (phase 1), consumption for the
requester (phase 2).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apps.api.routers import amaze, consumer, dev_identity, health
from ports.config import get_settings
from ports.identity import get_identity
from ports.scheduler import get_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # an invalid selector stops start-up here with a ConfigError
    app.state.identity = get_identity(settings)
    scheduler = get_scheduler(settings)
    app.state.scheduler = scheduler
    # A stub scheduler is not started; /api/health reports it as unavailable instead.
    runs_scheduler = not getattr(scheduler, "is_stub", False)
    if runs_scheduler:
        scheduler.start()
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


app.include_router(health.router)
app.include_router(dev_identity.router)
app.include_router(amaze.router)
app.include_router(consumer.router)
