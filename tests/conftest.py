"""Shared test fixtures.

Database tests run against the real local PostgreSQL, in the database named by TEST_DATABASE_URL
(never DATABASE_URL). The schema is rebuilt with Alembic once per test run, and every test runs
inside a transaction that is rolled back. If the database is unreachable, tests fail; they do not skip.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uuid

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


# ---------------------------------------------------------------- API tests (httpx, no Starlette TestClient)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class NoGlossaryRuns:
    """A do-nothing glossary runs worker (phase 5 plan, assumption 48): no test starts a worker on the
    application database. kicked is set when the start-up kick (or any kick) reaches it."""

    def __init__(self):
        import threading

        self.kicked = threading.Event()

    def drain(self) -> dict:
        self.kicked.set()
        return {}


@asynccontextmanager
async def running_app(engine=None, settings=None, session=None, glossary_runs=None):
    """Start the API (lifespan included) with optional overrides and yield an httpx client.

    session: every request uses this session (the test's rolled-back db_session), so a request sees
    rows the test created without committing them.
    glossary_runs: the step 05 worker; a do-nothing one unless the test gives its own.
    """
    import httpx

    from apps.api.deps import get_app_settings, get_db_engine, get_db_session
    from apps.api.main import app

    app.state.glossary_runs = glossary_runs or NoGlossaryRuns()
    if engine is not None:
        app.dependency_overrides[get_db_engine] = lambda: engine
    if settings is not None:
        app.dependency_overrides[get_app_settings] = lambda: settings
    if session is not None:
        app.dependency_overrides[get_db_session] = lambda: session
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                yield client
    finally:
        app.dependency_overrides.clear()
        del app.state.glossary_runs


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
        if payload is None:  # 0016: a glossary_element or mapping proposal names its column
            payload = {"note": "synthetic"}
            if kind in ("glossary_element", "mapping"):
                payload["source_field"] = f"synthetic_{uuid.uuid4().hex[:8]}"
        return ProposalRepository(db_session).create(
            proposal_log_id=logged_call.id, kind=kind, payload=payload,
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


# ---------------------------------------------------------------- phase 3: a local HTTP source


@pytest.fixture
def local_http_server():
    """An HTTP server on 127.0.0.1 (a free port), in a thread, for the HTTP connector.

    Serve a path by setting server.files[path] = bytes; any other path gives 404. Every request path is
    appended to server.requests. Never reaches the internet (phase 3 plan, assumption 16)."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    files: dict[str, bytes] = {}
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server's name)
            requests.append(self.path)
            body = files.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            self.wfile.write(body or b"")

        def log_message(self, *args):  # keep the test output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.files, server.requests = files, requests
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------- phase 2: glossary and products


@pytest.fixture
def glossary(db_session):
    """The 33 seed elements, inside the test's transaction."""
    from core.repositories.glossary import GlossaryRepository
    from core.seed import seed_glossary

    seed_glossary(db_session)
    return GlossaryRepository(db_session)


@pytest.fixture
def make_product(db_session, glossary):
    """Create a product; by default open, owned by dev-data-owner and holding close_price."""
    from core.repositories.products import ProductRepository

    def _make(code="synthetic_prices", *, name=None, element_keys=("close_price",), **more):
        fields = {"name": name or code.replace("_", " ").capitalize(), "domain": "pricing",
                  "owner_id": "dev-data-owner", "visibility": "open", **more}
        return ProductRepository(db_session).create(code=code, element_keys=list(element_keys), **fields)

    return _make
