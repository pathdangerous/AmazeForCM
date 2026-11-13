"""Amaze for CM PoC API. Run: .venv\\Scripts\\python -m uvicorn apps.api.main:app --port 8000

Phase 0: health and dev identity only. Per-platform routers arrive with the phases that need them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.routers import dev_identity, health
from ports.config import get_settings
from ports.identity import get_identity
from ports.scheduler import get_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # an invalid selector stops start-up here with a ConfigError
    app.state.identity = get_identity(settings)
    app.state.scheduler = get_scheduler(settings)
    app.state.scheduler.start()
    try:
        yield
    finally:
        app.state.scheduler.shutdown()


app = FastAPI(title="Amaze for CM PoC API", version="0.0.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(dev_identity.router)
