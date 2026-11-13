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
from sqlalchemy import create_engine, text
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
    """Test database rebuilt from scratch: empty schema, then upgrade to head.

    The schema is dropped rather than downgraded, so a migration edited during development (before it
    reaches the application database) cannot break the next run. Downgrades are tested separately in
    test_migrations.py. test_database_url has already refused the application database.
    """
    engine = create_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config(test_database_url), "head")
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


# ---------------------------------------------------------------- phase 1: proposals and approvals


def dev_user_for(role: str):
    """The seeded dev user who holds this role (dev-data-steward for data_steward, and so on)."""
    from ports.identity.dev import DevIdentity

    user = DevIdentity().get_user("dev-" + role.replace("_", "-"))
    assert user is not None and role in user.roles, role
    return user


@pytest.fixture
def logged_call(db_session):
    """One logged model call (a proposal_log row) made with the MockProvider smoke fixture."""
    from pydantic import BaseModel

    from core.proposal_log import ProposalLog
    from ports.llm.base import Message
    from ports.llm.mock import MockProvider

    class SmokeOutput(BaseModel):
        summary: str
        confidence: float

    result = MockProvider().complete(
        prompt_id="phase0_smoke", prompt_version="v1",
        messages=[Message(role="user", content="hi")], response_schema=SmokeOutput,
    )
    return ProposalLog(db_session).record(agent_name="SmokeAgent", input_ref="blob://inputs/1", result=result)


@pytest.fixture
def make_proposal(db_session, logged_call):
    """Create a pending proposal linked to logged_call."""
    from core.repositories.proposals import ProposalRepository

    def _make(kind="mapping", roles=("data_steward",), autonomy="A2", payload=None, **links):
        return ProposalRepository(db_session).create(
            proposal_log_id=logged_call.id, kind=kind, payload=payload or {"note": "synthetic"},
            autonomy=autonomy, required_approver_roles=list(roles), **links,
        )

    return _make


@pytest.fixture
def approved_proposal(db_session, make_proposal):
    """Create a proposal and have each required role's dev user approve it, in order."""
    from core.repositories.proposals import ProposalRepository

    def _approve(kind="mapping", roles=("data_steward",), **links):
        proposal = make_proposal(kind=kind, roles=roles, **links)
        repo = ProposalRepository(db_session)
        for role in roles:
            repo.record_decision(proposal.id, user=dev_user_for(role), decision="approved")
        return repo.get(proposal.id)

    return _approve
