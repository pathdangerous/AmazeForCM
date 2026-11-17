"""One schema per pytest worker (phase 7 plan, assumptions 2 and 3).

Every database fixture of a worker sits in its own schema of amaze_test, named w_<worker id> (w_gw0, w_gw1, ...;
w_master without -n). No test reads another worker's schema, and no test file names a schema itself.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.conftest import fresh_schema

TESTS = Path(__file__).resolve().parent
WORKER_SCHEMA = "w_" + os.environ.get("PYTEST_XDIST_WORKER", "master")

# The two helpers that make their own uuid-named schemas (assumption 2); they and conftest.py may name a schema.
SCHEMA_HELPERS = {"fresh_schema", "scratch_schema"}
# Where a schema is named: 'public' beside a catalogue column or cast ("visibility" is also 'public' in many
# tests, and is not a schema), a worker schema name, a search_path, or a pg_namespace name comparison.
SCHEMA_LITERALS = [
    re.compile(r"""(?:nspname|schemaname|table_schema|schema_name)\s*(?:=|IN)\s*\(?\s*['"]public['"]""", re.I),
    re.compile(r"""['"]public['"]\s*::\s*regnamespace""", re.I),
    re.compile(r"""\bnspname\s*="""),
    re.compile(r"""\bw_(?:gw\d+|master)\b"""),
    re.compile(r"""search_path\s*="""),
    re.compile(r"""\bSET\s+search_path\b""", re.I),
]


def test_the_engine_and_the_session_see_only_this_workers_schema(migrated_engine, db_session):
    with migrated_engine.connect() as connection:
        engine_view = connection.execute(text("SELECT current_schema(), current_setting('search_path')")).one()
    session_view = db_session.execute(text("SELECT current_schema(), current_setting('search_path')")).one()
    assert tuple(engine_view) == (WORKER_SCHEMA, WORKER_SCHEMA)
    assert tuple(session_view) == (WORKER_SCHEMA, WORKER_SCHEMA)


def test_the_migrated_tables_are_in_this_workers_schema_only(migrated_engine):
    with migrated_engine.connect() as connection:
        schemas = connection.execute(text(
            "SELECT DISTINCT table_schema FROM information_schema.tables WHERE table_name = 'feeds' "
            "AND table_schema = ANY(SELECT nspname FROM pg_namespace WHERE nspname = :mine)"), {"mine": WORKER_SCHEMA}
        ).scalars().all()
        visible = connection.execute(text(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'feeds' "
            "AND table_schema = current_schema()")).scalar_one()
    assert schemas == [WORKER_SCHEMA]
    assert visible == 1


def _lock_source_syn_x(session):
    from core.steps.known_source import lock_source

    lock_source(session, "syn_lock_key_check")


def _lock_glossary(session):
    from core.repositories.glossary import GlossaryRepository

    GlossaryRepository(session).lock()


LOCKS = pytest.mark.parametrize("take", [_lock_source_syn_x, _lock_glossary], ids=["source_lock", "glossary_lock"])


def _second_takes_the_lock(take, first_engine, second_engine) -> None:
    """The first session takes the lock and holds it (open transaction); the second takes it with a 300 ms
    lock_timeout, so waiting shows as LockNotAvailable and not waiting as a return."""
    with Session(first_engine) as first, Session(second_engine) as second:
        take(first)
        second.execute(text("SET LOCAL lock_timeout = '300ms'"))
        take(second)


@LOCKS
def test_the_same_lock_in_two_schemas_has_two_keys_and_nobody_waits(test_database_url, take):
    with fresh_schema(test_database_url, "lk") as (first, _, _), fresh_schema(test_database_url, "lk") as (second, _, _):
        _second_takes_the_lock(take, first, second)


@LOCKS
def test_the_same_lock_in_one_schema_makes_the_second_session_wait(test_database_url, take):
    with fresh_schema(test_database_url, "lk") as (engine, _, _):
        with pytest.raises(OperationalError, match="lock timeout"):
            _second_takes_the_lock(take, engine, engine)


def _helper_lines(path: Path) -> set[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in SCHEMA_HELPERS:
            lines.update(range(node.lineno, node.end_lineno + 1))
    return lines


def test_no_test_file_names_a_schema_outside_conftest_and_the_two_helpers():
    found = []
    for path in sorted(TESTS.glob("*.py")):
        if path.name in ("conftest.py", Path(__file__).name):
            continue
        skip = _helper_lines(path)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if number not in skip and any(pattern.search(line) for pattern in SCHEMA_LITERALS):
                found.append(f"{path.name}:{number}: {line.strip()}")
    assert found == []
