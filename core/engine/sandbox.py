"""The DuckDB sandbox for agent-proposed SQL (phase 4 plan, section 16 note 3; phase 6 plan, assumptions 5 and 6).

A validation rule is SQL written by an agent and approved by two people, but it is still text from a model, so
it runs only here, in an in-memory DuckDB connection that cannot reach anything but the one file it was given:

    1. the connection is made with extension auto-install and auto-load off;
    2. the one file is read into memory (load_csv), and the trusted statements of the engine that build the
       tables the rules read (execute) run;
    3. lock() switches external access off and locks the configuration, so it cannot be switched on again:
       no file (the same one included), no network, no COPY ... TO, ATTACH, INSTALL or LOAD;
    4. only then run_rule() runs a rule, and only if it is exactly one plain SELECT with a `_row` column,
       under a time limit (connection.interrupt() from a timer).

No model call and no database connection here (rule 2). The DuckDB version is pinned in requirements.txt; the
statement check below relies on how it parses a PRAGMA (see check_rule_sql).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import duckdb


class RuleRefused(Exception):
    """The rule is not a plain SELECT with a `_row` column. Nothing ran (or only a SELECT that was then refused)."""


class RuleFailed(Exception):
    """DuckDB refused or failed the rule (a missing column, an operation the lock does not allow, ...)."""


class RuleTimedOut(RuleFailed):
    """The rule ran longer than the time limit and was interrupted."""


class FileUnreadable(Exception):
    """DuckDB could not read the file as a CSV table (the engine's raw stage fails with it)."""


def _plain(text: str) -> str:
    return text.strip().rstrip(";").strip()


def check_rule_sql(sql: str) -> None:
    """Exactly one statement, of type SELECT, that DuckDB did not rewrite. DuckDB types `PRAGMA database_size`
    as SELECT but rewrites it to `SELECT * FROM pragma_database_size();`, so the statement's own text must be
    the text given; a plain SELECT (or WITH, VALUES, FROM-first) is parsed as written."""
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise RuleRefused(f"not parsed as SQL: {_first_line(exc)}") from None
    if len(statements) != 1:
        raise RuleRefused(f"a rule is exactly one statement, not {len(statements)}")
    statement = statements[0]
    if statement.type != duckdb.StatementType.SELECT:
        raise RuleRefused(f"a rule is a SELECT, not {statement.type.name}")
    if _plain(statement.query) != _plain(sql):
        raise RuleRefused("a rule is a plain SELECT; DuckDB rewrote this statement (a PRAGMA?)")


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__


RAW_LOAD_SQL = ('CREATE TABLE "{table}" AS SELECT row_number() OVER () AS _row, * '
                "FROM read_csv($path, header = true, all_varchar = true)")


def raw_load_sql(table: str = "raw") -> str:
    """The one statement that reads the file into memory. Generated code (core/engine/codegen.py) shows this same text."""
    return RAW_LOAD_SQL.format(table=table)


class Sandbox:
    def __init__(self, *, timeout_seconds: float):
        self._timeout = timeout_seconds
        self._locked = False
        self.executed: list[str] = []  # every statement run, in order: what a test compares with the generated code
        self._connection = duckdb.connect(
            ":memory:", config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})

    def load_csv(self, path: Path, table: str = "raw") -> None:
        """Read the one file into memory: every column as text (the engine casts), plus `_row`, the file's data
        row number from 1 in file order. Before lock() only."""
        self._require_unlocked()
        self.executed.append(raw_load_sql(table))
        try:
            self._connection.execute(raw_load_sql(table), {"path": str(path)})
        except duckdb.Error as exc:
            raise FileUnreadable(_first_line(exc)) from None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> list[tuple]:
        """A trusted statement of the engine (never rule text), before lock() only."""
        self._require_unlocked()
        self.executed.append(sql)
        return self._connection.execute(sql, params or {}).fetchall()

    def lock(self) -> None:
        """No file or network access from here on, and no way to switch it back on."""
        self._connection.execute("SET enable_external_access = false")
        self._connection.execute("SET lock_configuration = true")
        self._locked = True

    def run_rule(self, sql: str) -> list[int]:
        """The `_row` of every row that breaks the rule, sorted, once each."""
        if not self._locked:
            raise RuntimeError("the sandbox is not locked: lock() before any rule runs")
        check_rule_sql(sql)
        self.executed.append(sql)
        timer = threading.Timer(self._timeout, self._connection.interrupt)
        timer.start()
        try:
            cursor = self._connection.execute(sql)
            if "_row" not in [column[0] for column in cursor.description]:
                raise RuleRefused("a rule returns the `_row` of every row that breaks it; this has no _row column")
            rows = cursor.fetchall()
            position = [column[0] for column in cursor.description].index("_row")
        except duckdb.InterruptException:
            raise RuleTimedOut(f"stopped after {self._timeout} s") from None
        except duckdb.Error as exc:
            raise RuleFailed(_first_line(exc)) from None
        finally:
            timer.cancel()
        return sorted({int(row[position]) for row in rows if row[position] is not None})

    def close(self) -> None:
        self._connection.close()

    def _require_unlocked(self) -> None:
        if self._locked:
            raise RuntimeError("the sandbox is locked: only run_rule() is allowed now")
