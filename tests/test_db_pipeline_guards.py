"""Migration 0037 (phase 7.17, T2; plan docs/plans/phase-7.17.md, 2.2): pipeline_runs is append-only and its outcome is
recomputed from its stages; a new go-live needs a passed run of the code version it pins. By raw SQL on the test's
session; each refusal is checked by its message."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.configured import a_registered_feed
from tests.test_db_go_live_guards import EMPTY_SHA256, GO_LIVE, a_code_version, s, summary_proposal, world  # noqa: F401

STAGES = ("commit", "sql_parse", "rule_check", "test_load")
SHA = "ab" * 32
COMMIT = "c0ffee" * 6 + "c0ff"
INSERT = ("INSERT INTO pipeline_runs (feed_id, code_sha256, commit_hash, commit_created, stages, outcome, run_by) "
          "VALUES (:f, :sha, :commit, true, CAST(:stages AS jsonb), :outcome, :by) RETURNING id")


def stages_of(*statuses: str) -> list[dict]:
    return [{"stage": name, "status": status, "log": [f"{name} {status}"]} for name, status in zip(STAGES, statuses)]


def add_run(session, feed, *, statuses=("passed",) * 4, outcome="passed", commit=COMMIT, sha=SHA, by="dev-operator",
            stages=None):
    return session.execute(text(INSERT), {"f": feed, "sha": sha, "commit": commit, "outcome": outcome, "by": by,
                                          "stages": json.dumps(stages if stages is not None else stages_of(*statuses))}).scalar_one()


def refused(session, feed, match: str, **over):
    with pytest.raises(DBAPIError, match=match):
        with session.begin_nested():
            add_run(session, feed, **over)


@pytest.fixture
def feed(s):  # noqa: F811
    return a_registered_feed(s, "syn_pipeline_guards")


def test_a_passed_run_is_stored(s, feed):
    add_run(s, feed)
    assert s.execute(text("SELECT outcome, commit_hash FROM pipeline_runs WHERE feed_id = :f"), {"f": feed}).one() == ("passed", COMMIT)


@pytest.mark.parametrize(("statuses", "outcome", "commit"), [
    (("failed", "skipped", "skipped", "skipped"), "failed_at_commit", None),
    (("passed", "failed", "skipped", "skipped"), "failed_at_sql_parse", COMMIT),
    (("passed", "passed", "failed", "skipped"), "failed_at_rule_check", COMMIT),
    (("passed", "passed", "passed", "failed"), "failed_at_test_load", COMMIT),
])
def test_a_run_that_failed_at_a_stage_is_stored_with_the_later_stages_skipped(s, feed, statuses, outcome, commit):
    add_run(s, feed, statuses=statuses, outcome=outcome, commit=commit)
    assert s.execute(text("SELECT outcome FROM pipeline_runs WHERE feed_id = :f"), {"f": feed}).scalar_one() == outcome


@pytest.mark.parametrize(("case", "over", "match"), [
    ("an outcome that is not the stages'", {"statuses": ("passed", "failed", "skipped", "skipped"), "outcome": "passed"}, "outcome"),
    ("a failure named at another stage", {"statuses": ("passed", "failed", "skipped", "skipped"),
                                         "outcome": "failed_at_test_load"}, "outcome"),
    ("a stage after a failure that is not skipped", {"statuses": ("passed", "failed", "passed", "skipped"),
                                                      "outcome": "failed_at_sql_parse"}, "stages"),
    ("a stage before a failure that is not passed", {"statuses": ("passed", "skipped", "failed", "skipped"),
                                                      "outcome": "failed_at_rule_check"}, "stages"),
    ("a stage that is not passed, failed or skipped", {"statuses": ("passed", "ok", "passed", "passed")}, "stages"),
    ("three stages", {"stages": stages_of("passed", "passed", "passed")}, "stages"),
    ("stages out of order", {"stages": list(reversed(stages_of(*("passed",) * 4)))}, "stages"),
    ("a passed run with no commit", {"commit": None}, "commit"),
    ("a commit that is not a hash", {"commit": "main"}, "commit"),
    ("a code hash that is not a SHA-256", {"sha": "xyz"}, "code_sha256|ck_pipeline_runs"),
    ("nobody who ran it", {"by": "  "}, "run_by|ck_pipeline_runs"),
])
def test_a_run_whose_stages_do_not_add_up_is_refused(s, feed, case, over, match):
    refused(s, feed, match, **over)


@pytest.mark.parametrize("sql", ["UPDATE pipeline_runs SET run_by = 'x'", "DELETE FROM pipeline_runs", "TRUNCATE pipeline_runs"])
def test_a_run_is_append_only(s, feed, sql):
    add_run(s, feed)
    with pytest.raises(DBAPIError, match="append-only|restrict|truncate"):
        with s.begin_nested():
            s.execute(text(sql))


# ---------------------------------------------------------------- the go-live needs a passed run of the code it pins


def go_live_params(w, proposal):
    """A go-live that pins a code version of the empty files (hash EMPTY_SHA256): no pipeline run is made here."""
    p = {"feed": w.feed, "version": 1, "proposal": proposal.id, "by": "dev-data-owner", "mappings": w.mappings,
         "rules": w.rules, "load": w.load, "until": date.today() + timedelta(days=10)}
    return {**p, "code": a_code_version(w.s, p, with_run=False)}


def go_live_refused(w, params):
    with pytest.raises(DBAPIError, match="has no passed pipeline run"):
        with w.s.begin_nested():
            w.s.execute(text(GO_LIVE), params)


def test_a_go_live_with_no_run_is_refused(world):  # noqa: F811
    go_live_refused(world, go_live_params(world, summary_proposal(world.s, world)))


@pytest.mark.parametrize(("statuses", "outcome"), [
    (("failed", "skipped", "skipped", "skipped"), "failed_at_commit"),
    (("passed", "failed", "skipped", "skipped"), "failed_at_sql_parse"),
    (("passed", "passed", "failed", "skipped"), "failed_at_rule_check"),
    (("passed", "passed", "passed", "failed"), "failed_at_test_load"),
])
def test_a_run_that_failed_at_any_stage_does_not_let_the_go_live_through(world, statuses, outcome):  # noqa: F811
    add_run(world.s, world.feed, statuses=statuses, outcome=outcome, sha=EMPTY_SHA256, commit=None if statuses[0] == "failed" else COMMIT)
    go_live_refused(world, go_live_params(world, summary_proposal(world.s, world)))


def test_a_passed_run_of_other_code_does_not_count(world):  # noqa: F811
    add_run(world.s, world.feed, sha=SHA)  # the pinned code version has EMPTY_SHA256
    go_live_refused(world, go_live_params(world, summary_proposal(world.s, world)))


def test_a_passed_run_of_another_feed_does_not_count(world):  # noqa: F811
    add_run(world.s, a_registered_feed(world.s, "syn_pipeline_other"), sha=EMPTY_SHA256)
    go_live_refused(world, go_live_params(world, summary_proposal(world.s, world)))


def test_a_passed_run_of_the_pinned_code_lets_the_go_live_through(world):  # noqa: F811
    add_run(world.s, world.feed, sha=EMPTY_SHA256)
    params = go_live_params(world, summary_proposal(world.s, world))
    assert world.s.execute(text(GO_LIVE), params).scalar_one() is not None
