"""Shared test fixtures.

Database tests run against the real local PostgreSQL, in the database named by TEST_DATABASE_URL
(never DATABASE_URL). The schema is rebuilt with Alembic once per test run, and every test runs
inside a transaction that is rolled back. If the database is unreachable, tests fail; they do not skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from ports.secrets.base import SecretNotFound
from ports.secrets.env import EnvSecrets

REPO_ROOT = Path(__file__).resolve().parents[1]


def alembic_config(url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def test_database_url() -> str:
    secrets = EnvSecrets()
    try:
        url = secrets.get("TEST_DATABASE_URL")
    except SecretNotFound:
        pytest.fail("TEST_DATABASE_URL is not set in .env; see .env.example", pytrace=False)
    try:
        app_db = make_url(secrets.get("DATABASE_URL")).database
    except SecretNotFound:
        app_db = None
    if make_url(url).database == app_db:
        pytest.fail("TEST_DATABASE_URL must not name the application database", pytrace=False)
    return url


@pytest.fixture(scope="session")
def migrated_engine(test_database_url) -> Engine:
    """Test database rebuilt from scratch: downgrade to base, then upgrade to head."""
    config = alembic_config(test_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(test_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine) -> Session:
    """A session whose work is rolled back at the end of the test, commits included."""
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
