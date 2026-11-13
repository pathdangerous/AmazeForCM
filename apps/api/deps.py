"""FastAPI dependencies. Tests override get_app_settings and get_db_engine."""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy.engine import Engine

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


def get_identity_port(request: Request) -> Identity:
    return request.app.state.identity


def current_user(request: Request) -> User:
    user = get_identity_port(request).authenticate(request.headers)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user
