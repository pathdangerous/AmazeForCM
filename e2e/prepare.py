"""Rebuild the story's world for one Playwright run (phase 7 plan, assumption 39, rows E).

    python -m e2e.prepare

Schema `e2e_story` of the test database `amaze_test` is dropped and rebuilt (alembic upgrade head, the glossary
seed, the demo products); the synthetic sources are copied to data/e2e/sources and data/e2e/blob is emptied. The
application database is never touched: the same refusal as the tests make (TEST_DATABASE_URL must not name
DATABASE_URL's database). e2e/serve_api.py runs this before it starts the API, so the API of a run always starts
on a fresh story.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "e2e_story"
SOURCES = REPO_ROOT / "data" / "e2e" / "sources"
BLOB = REPO_ROOT / "data" / "e2e" / "blob"


class NotTheTestDatabase(RuntimeError):
    """The story would run on the application's database: refused before anything is dropped."""


def story_url() -> str:
    """TEST_DATABASE_URL with the story schema as the whole search_path. NotTheTestDatabase if it is DATABASE_URL's."""
    from ports.secrets.base import SecretNotFound
    from ports.secrets.env import EnvSecrets

    secrets = EnvSecrets()
    try:
        url = make_url(secrets.get("TEST_DATABASE_URL"))
    except SecretNotFound:
        raise NotTheTestDatabase("TEST_DATABASE_URL is not set in .env; see .env.example") from None
    try:
        app_database = make_url(secrets.get("DATABASE_URL")).database
    except SecretNotFound:
        app_database = None
    if url.database == app_database:
        raise NotTheTestDatabase("TEST_DATABASE_URL names the application database; the story never runs there")
    return url.update_query_dict({"options": f"-csearch_path={SCHEMA}"}).render_as_string(hide_password=False)


def rebuild(url: str) -> None:
    """Drop and create the schema, migrate, seed. The caller has taken `url` from story_url()."""
    from core.seed import seed_demo, seed_glossary

    base = create_engine(make_url(url).difference_update_query(["options"]))
    with base.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    base.dispose()
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    with sessionmaker(engine)() as session:
        seed_glossary(session)
        seed_demo(session)
        session.commit()
    engine.dispose()
    shutil.rmtree(SOURCES, ignore_errors=True)
    shutil.copytree(REPO_ROOT / "fixtures" / "sources", SOURCES)
    shutil.rmtree(BLOB, ignore_errors=True)
    BLOB.mkdir(parents=True)
    # 7.17: the story's own pipeline repository starts empty (git's object files are read-only on Windows)
    pipeline_repo = REPO_ROOT / "data" / "e2e" / "pipeline-repo"
    if pipeline_repo.exists():
        shutil.rmtree(pipeline_repo, onexc=lambda func, path, _: (os.chmod(path, stat.S_IWRITE), func(path)))


def main() -> None:
    url = story_url()
    rebuild(url)
    print(f"story rebuilt: database={make_url(url).database} schema={SCHEMA}")


if __name__ == "__main__":
    try:
        main()
    except NotTheTestDatabase as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(1)
