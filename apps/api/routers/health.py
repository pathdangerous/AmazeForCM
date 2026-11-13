"""GET /api/health: API status, database reachability and the selected adapters."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from apps.api.deps import get_app_settings, get_db_engine
from ports.config import Settings
from ports.db import database_is_reachable

router = APIRouter()


class Health(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["ok", "unreachable", "not_configured"]
    adapters: dict[str, str]


@router.get("/api/health", response_model=Health)
def health(
    response: Response,
    settings: Annotated[Settings, Depends(get_app_settings)],
    engine: Annotated[Engine | None, Depends(get_db_engine)],
) -> Health:
    if engine is None:
        database = "not_configured"
    else:
        database = "ok" if database_is_reachable(engine) else "unreachable"
    if database != "ok":
        response.status_code = 503
    return Health(
        status="ok" if database == "ok" else "error",
        database=database,
        adapters={
            "llm": settings.llm_provider,
            "storage": settings.storage_provider,
            "scheduler": settings.scheduler_provider,
            "identity": settings.identity_provider,
            "secrets": settings.secrets_provider,
            "notify": settings.notify_provider,
        },
    )
