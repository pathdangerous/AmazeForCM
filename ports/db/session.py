"""Database engine and sessions. DATABASE_URL comes from the Secrets port, so Key Vault can supply it later.

The same code serves local PostgreSQL and Azure Database for PostgreSQL; only the URL changes.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ports.secrets import Secrets, get_secrets


def get_database_url(secrets: Secrets | None = None) -> str:
    return (secrets or get_secrets()).get("DATABASE_URL")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(get_database_url(), pool_pre_ping=True)


def get_sessionmaker(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


def database_is_reachable(engine: Engine | None = None) -> bool:
    try:
        with (engine or get_engine()).connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
