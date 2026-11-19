"""GET /api/health: API status, database reachability, the selected adapters, and which of them
cannot work yet (Azure stubs). 200 only when the database is ok and no adapter is unavailable."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from apps.api.deps import get_app_settings, get_db_engine
from core.repositories.settings import FourEyesRepository
from ports.config import Settings
from ports.db import database_is_reachable
from ports.identity import get_identity
from ports.llm import get_llm_provider
from ports.notify import get_notifier
from ports.scheduler import get_scheduler
from ports.secrets import get_secrets
from ports.storage import get_blob_store

router = APIRouter()


class Health(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["ok", "unreachable", "not_configured"]
    adapters: dict[str, str]
    unavailable: list[str]
    four_eyes: bool  # REQUIRE_FOUR_EYES as the database says it (phase 7.8); true when the database cannot say


def four_eyes_now(engine: Engine | None, database: str) -> bool:
    """The database's answer; fail safe (on) when there is no reachable database or no migration 0020 yet."""
    if engine is None or database != "ok":
        return True
    try:
        with Session(bind=engine) as session:
            return FourEyesRepository(session).current()
    except Exception:
        return True


def unavailable_adapters(settings: Settings) -> list[str]:
    """Names of the selected adapters that cannot work yet, in a fixed order: a stub, or a real adapter that reports
    available = False (not configured). Nothing here calls a model or a service."""
    selected = {
        "llm": get_llm_provider(settings),
        "storage": get_blob_store(settings),
        "scheduler": get_scheduler(settings),
        "identity": get_identity(settings),
        "secrets": get_secrets(settings),
        "notify": get_notifier(settings),
    }
    return [name for name, adapter in selected.items()
            if getattr(adapter, "is_stub", False) or getattr(adapter, "available", True) is False]


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
    unavailable = unavailable_adapters(settings)
    ok = database == "ok" and not unavailable
    if not ok:
        response.status_code = 503
    return Health(
        status="ok" if ok else "error",
        database=database,
        adapters={
            "llm": settings.llm_provider,
            "storage": settings.storage_provider,
            "scheduler": settings.scheduler_provider,
            "identity": settings.identity_provider,
            "secrets": settings.secrets_provider,
            "notify": settings.notify_provider,
        },
        unavailable=unavailable,
        four_eyes=four_eyes_now(engine, database),
    )
