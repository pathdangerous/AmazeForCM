"""FastAPI dependencies. Tests override get_app_settings, get_db_engine and get_db_session."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ports.config import Settings, get_settings
from ports.db import get_engine
from ports.identity import Identity, User
from ports.secrets import SecretNotFound


def get_app_settings() -> Settings:
    return get_settings()


def get_db_engine() -> Engine | None:
    """The database engine, or None if DATABASE_URL cannot be read (not set, or the Secrets
    adapter is still a stub)."""
    try:
        return get_engine()
    except (SecretNotFound, NotImplementedError):
        return None


def get_db_session(engine: Annotated[Engine | None, Depends(get_db_engine)]) -> Iterator[Session]:
    """One session per request, closed afterwards. Read endpoints never commit."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured")
    with Session(bind=engine) as session:
        yield session


def get_identity_port(request: Request) -> Identity:
    return request.app.state.identity


def current_user(request: Request) -> User:
    user = get_identity_port(request).authenticate(request.headers)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def backend_user(user: Annotated[User, Depends(current_user)]) -> User:
    """Amaze (backend-ui) endpoints: any backend role; the requester gets 403."""
    if user.app != "backend":
        raise HTTPException(status_code=403, detail="Backend role required")
    return user
