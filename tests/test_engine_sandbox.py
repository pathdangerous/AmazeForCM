"""The DuckDB sandbox for agent-proposed SQL (phase 4 plan, section 16 note 3; phase 6 plan, assumptions 5 and 6,
guard proof S1). The file is read into memory first; then external access is switched off and the configuration
locked; only then does any rule run, as exactly one plain SELECT with a `_row` column, under a time limit.
"""

from __future__ import annotations

import time

import pytest

from core.engine.sandbox import RuleFailed, RuleRefused, RuleTimedOut, Sandbox

CSV = "a,b\n1,x\n2,y\n3,z\n"


@pytest.fixture
def files(tmp_path):
    (tmp_path / "f.csv").write_text(CSV)
    (tmp_path / "other.csv").write_text("secret\nhidden\n")
    return tmp_path


@pytest.fixture
def sandbox(files):
    box = Sandbox(timeout_seconds=5)
    box.load_csv(files / "f.csv")
    box.lock()
    yield box
    box.close()


def test_a_select_over_the_file_returns_the_flagged_rows(sandbox):
    assert sandbox.run_rule("SELECT _row FROM raw WHERE b IN ('x', 'z')") == [1, 3]


def test_a_with_query_and_trailing_semicolon_are_plain_selects(sandbox):
    assert sandbox.run_rule("WITH bad AS (SELECT _row FROM raw WHERE a = '2') SELECT _row FROM bad;") == [2]


def test_the_rows_come_back_sorted_and_once(sandbox):
    assert sandbox.run_rule("SELECT _row FROM raw UNION ALL SELECT _row FROM raw WHERE a = '1'") == [1, 2, 3]


def test_the_file_rows_are_numbered_from_one_in_file_order(sandbox):
    assert sandbox.run_rule("SELECT _row FROM raw WHERE a = '3'") == [3]


def test_a_rule_runs_only_once_the_sandbox_is_locked(files):
    box = Sandbox(timeout_seconds=5)
    box.load_csv(files / "f.csv")
    with pytest.raises(RuntimeError, match="locked"):
        box.run_rule("SELECT _row FROM raw")
    box.close()


def test_a_rule_without_a_row_column_is_refused(sandbox):
    with pytest.raises(RuleRefused, match="_row"):
        sandbox.run_rule("SELECT a FROM raw")


# ---------------------------------------------------------------- S1: twelve things a rule must not do

# (rule sql, the error it ends in, a fragment of its message). {other} and {same} are paths of this test.
REFUSED = [
    ("SELECT _row FROM read_csv('{other}')", RuleFailed, "Permission Error"),          # 1 another file
    ("SELECT _row FROM read_csv('{same}')", RuleFailed, "Permission Error"),           # 2 the same file again
    ("COPY (SELECT 1 AS _row) TO '{other}.out.csv'", RuleRefused, "SELECT"),           # 3
    ("ATTACH '{other}.db' AS other", RuleRefused, "SELECT"),                           # 4
    ("INSTALL httpfs", RuleRefused, "SELECT"),                                         # 5
    ("LOAD httpfs", RuleRefused, "SELECT"),                                            # 6
    ("SELECT _row FROM read_csv('http://127.0.0.1:9/x.csv')", RuleFailed, "Permission Error"),  # 7
    ("SELECT file AS _row FROM glob('*')", RuleFailed, "Permission Error"),            # 8
    ("SET enable_external_access = true", RuleRefused, "SELECT"),                      # 9
    ("SELECT _row FROM raw; SELECT _row FROM raw", RuleRefused, "exactly one"),        # 10 two statements
    ("DELETE FROM raw", RuleRefused, "SELECT"),                                        # 11
    ("PRAGMA database_size", RuleRefused, "plain SELECT"),                             # 12
]


@pytest.mark.parametrize(("sql", "error", "fragment"), REFUSED, ids=[
    "read another file", "read the same file", "COPY TO", "ATTACH", "INSTALL", "LOAD", "http read", "glob",
    "SET enable_external_access", "two statements", "DELETE", "PRAGMA"])
def test_a_rule_that_reaches_outside_the_file_or_changes_anything_is_refused(sandbox, files, sql, error, fragment):
    with pytest.raises(error, match=fragment):
        sandbox.run_rule(sql.format(other=files / "other.csv", same=files / "f.csv"))
    assert sandbox.run_rule("SELECT _row FROM raw WHERE a = '1'") == [1]  # and the sandbox still works


def test_the_other_file_was_not_written_or_read(sandbox, files):
    with pytest.raises(RuleRefused):
        sandbox.run_rule(f"COPY (SELECT 1 AS _row) TO '{files / 'other.csv'}.out.csv'")
    assert not (files / "other.csv.out.csv").exists()


def test_a_rule_over_the_time_limit_is_stopped(files):
    box = Sandbox(timeout_seconds=0.2)
    box.load_csv(files / "f.csv")
    box.lock()
    started = time.monotonic()
    with pytest.raises(RuleTimedOut, match="0.2"):
        box.run_rule("SELECT a._row FROM (SELECT range AS _row FROM range(100000)) a, "
                     "(SELECT range AS b FROM range(100000)) c WHERE a._row + c.b = -1")
    assert time.monotonic() - started < 3
    assert box.run_rule("SELECT _row FROM raw WHERE a = '2'") == [2]  # the next rule still runs
    box.close()


def test_a_trusted_statement_before_the_lock_may_build_the_curated_table(files):
    box = Sandbox(timeout_seconds=5)
    box.load_csv(files / "f.csv")
    box.execute("CREATE TABLE curated AS SELECT _row, CAST(a AS BIGINT) AS a FROM raw")
    box.lock()
    assert box.run_rule("SELECT _row FROM curated WHERE a > 1") == [2, 3]
    box.close()
