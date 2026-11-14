"""FastAPI dependencies. Tests override get_app_settings, get_db_engine and get_db_session."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ports.config import Settings, get_settings
from ports.connectors import SourceConnector, get_connector
from ports.connectors.catalogue import SourceCatalogue
from ports.db import get_engine
from ports.identity import Identity, User
from ports.notify import Notifier, get_notifier
from ports.secrets import SecretNotFound
from ports.storage import BlobStore, get_blob_store


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


def requester_user(user: Annotated[User, Depends(current_user)]) -> User:
    """Consumption (consumer-ui) endpoints: the requester only; every backend role gets 403."""
    if user.app != "consumer":
        raise HTTPException(status_code=403, detail="Requester role required")
    return user


def get_request_notifier(
    session: Annotated[Session, Depends(get_db_session)], settings: Annotated[Settings, Depends(get_app_settings)]
) -> Notifier:
    """The Notifier selected by NOTIFY_PROVIDER, on the request's session, so in-app notifications are
    committed or rolled back with the rest of the request."""
    return get_notifier(settings, session=session)


# ---- phase 3: the custom data product. Each is a dependency so tests can replace it.


def get_source_catalogue(settings: Annotated[Settings, Depends(get_app_settings)]) -> SourceCatalogue:
    return SourceCatalogue.load(settings.source_catalogue, allowed_hosts=settings.allowed_source_hosts())


def get_blob_store_port(settings: Annotated[Settings, Depends(get_app_settings)]) -> BlobStore:
    return get_blob_store(settings)


def get_connector_factory(settings: Annotated[Settings, Depends(get_app_settings)]) -> Callable[[str], SourceConnector]:
    return lambda name: get_connector(name, settings)
