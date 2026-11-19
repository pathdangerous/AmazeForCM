"""agents/evaluate_agents.py and the gold sets in fixtures/gold/<prompt_id>.json (phase 7.8, S8).

Offline only: the MockProvider and the test database. The live run is the developer's (README: Live evaluation).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re

import pytest
from sqlalchemy import func, select

from agents import evaluate_agents as ev
from core.repositories.tables import ProposalLogEntry, ProposalRow
from ports.config import REPO_ROOT
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.step_chains import end_read

GOLD_DIR = REPO_ROOT / "fixtures" / "gold"
PROMPTS = ["intent", "source_discovery", "discovery_assistant", "profiler", "glossary_builder", "transformation",
           "rule_recommender", "review_summariser", "table_extractor"]
EXPECTED_CASES = {p: 6 for p in PROMPTS} | {"table_extractor": 3}
NAMES = re.compile(r"bloomberg|refinitiv|reuters|factset|morningstar|msci|s&p|moody|fitch|markit|nasdaq|nyse|"
                   r"lseg|sec\.gov|edgar|goldman|jpmorgan|citi", re.IGNORECASE)


def gold(prompt_id):
    return json.loads((GOLD_DIR / f"{prompt_id}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- the gold files


@pytest.mark.parametrize("prompt_id", PROMPTS)
def test_the_gold_file_validates_against_the_agents_input_schema(prompt_id):
    data = gold(prompt_id)
    assert set(data) == {"version", "description", "cases"} and data["version"] == 1 and data["description"]
    schema = ev.EVALUATORS[prompt_id].input_schema
    for case in data["cases"]:
        assert set(case) == {"id", "input", "checks"}
        schema.model_validate(case["input"])


@pytest.mark.parametrize("prompt_id", PROMPTS)
def test_the_case_count_is_the_required_one(prompt_id):
    assert len(gold(prompt_id)["cases"]) == EXPECTED_CASES[prompt_id] == ev.REQUIRED_CASES[prompt_id]


@pytest.mark.parametrize("prompt_id", PROMPTS)
def test_every_check_uses_the_vocabulary_and_ids_are_unique(prompt_id):
    cases = gold(prompt_id)["cases"]
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == len(ids)
    for case in cases:
        assert case["checks"], case["id"]
        for check in case["checks"]:
            assert check["check"] in ev.CHECKS, check
            ev.validate_check(check)  # the arguments it needs, and no others


@pytest.mark.parametrize("prompt_id", PROMPTS)
def test_the_gold_file_is_synthetic(prompt_id):
    text = (GOLD_DIR / f"{prompt_id}.json").read_text(encoding="utf-8")
    assert not NAMES.search(text)

    def codes(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("source_code", "feed_code") and isinstance(value, str):
                    yield value
                yield from codes(value)
        elif isinstance(node, list):
            for item in node:
                yield from codes(item)

    for code in codes(json.loads(text)):
        assert code.startswith("syn_"), code


def test_the_scanned_pages_hash_to_their_png_bytes():
    for case in gold("table_extractor")["cases"]:
        data = case["input"]
        for image, digest in zip(data["page_images"], data["page_hashes"], strict=True):
            png = base64.b64decode(image)
            assert png[:8] == b"\x89PNG\r\n\x1a\n" and hashlib.sha256(png).hexdigest() == digest


def test_each_prompt_has_one_read_only_evaluator_without_its_own_run():
    for prompt_id, cls in ev.EVALUATORS.items():
        assert (cls.prompt_id, cls.approver_roles) == (prompt_id, ())
        assert "run" not in cls.__dict__
    assert set(ev.EVALUATORS) == set(PROMPTS)


# ---------------------------------------------------------------- the check vocabulary


def test_the_checks_read_the_output_by_path():
    out = {"a": {"b": [{"k": "x", "n": 3}, {"k": "y", "n": 5}]}, "s": "Hello", "empty": []}
    ok = ev.check_passes
    assert ok({"check": "equals", "path": "s", "value": "Hello"}, out)
    assert not ok({"check": "equals", "path": "s", "value": "Bye"}, out)
    assert ok({"check": "equals", "path": "a.b.1.k", "value": "y"}, out)
    assert ok({"check": "equals", "path": "empty", "value": []}, out)
    assert ok({"check": "in", "path": "a.b.*.k", "values": ["x", "y", "z"]}, out)
    assert not ok({"check": "in", "path": "a.b.*.k", "values": ["x"]}, out)
    assert ok({"check": "contains", "path": "a.b.*.k", "value": "y"}, out)
    assert ok({"check": "contains", "path": "s", "value": "ell"}, out)
    assert ok({"check": "not_contains", "path": "a.b.*.k", "value": "z"}, out)
    assert ok({"check": "nonempty", "path": "a.b"}, out) and not ok({"check": "nonempty", "path": "empty"}, out)
    assert not ok({"check": "nonempty", "path": "missing"}, out)
    assert ok({"check": "count_at_least", "path": "a.b", "n": 2}, out)
    assert ok({"check": "count_at_most", "path": "a.b", "n": 2}, out)
    assert ok({"check": "number_at_most", "path": "a.b.0.n", "value": 3}, out)
    assert ok({"check": "number_at_least", "path": "a.b.1.n", "value": 5}, out)
    assert ok({"check": "matches", "path": "a.b.*.k", "pattern": "^[xy]$"}, out)
    assert ok({"check": "not_matches", "path": "a.b.*.k", "pattern": "z"}, out)


def test_the_transformation_and_glossary_checks():
    steps = {"columns": [
        {"source_field": "d", "steps": [{"op": "trim"}, {"op": "parse_date", "format": "DD-MM-YYYY"},
                                        {"op": "placeholder_to_null", "values": ["NA", "-"]}]},
        {"source_field": "c", "steps": []}]}
    ok = ev.check_passes
    assert ok({"check": "op_present", "column": "d", "op": "parse_date", "arg": "DD-MM-YYYY"}, steps)
    assert not ok({"check": "op_present", "column": "d", "op": "parse_date", "arg": "MM-DD-YYYY"}, steps)
    assert ok({"check": "op_present", "column": "d", "op": "placeholder_to_null", "arg": "NA"}, steps)
    assert ok({"check": "op_present", "column": "d", "op": "trim"}, steps)
    assert ok({"check": "op_absent", "column": "d", "op": "cast"}, steps)
    assert ok({"check": "no_steps", "column": "c"}, steps)
    assert not ok({"check": "no_steps", "column": "d"}, steps)
    assert not ok({"check": "op_present", "column": "nope", "op": "trim"}, steps)
    answers = {"answers": [{"column": "a", "same_as": "isin", "new_element": None},
                           {"column": "b", "same_as": None, "new_element": {"key": "best_eps"}},
                           {"column": "c", "same_as": None, "new_element": None}]}
    assert ok({"check": "option_kind", "column": "a", "kind": "same_as", "key": "isin"}, answers)
    assert not ok({"check": "option_kind", "column": "a", "kind": "same_as", "key": "cusip"}, answers)
    assert ok({"check": "option_kind", "column": "a", "kind": "same_as"}, answers)
    assert ok({"check": "option_kind", "column": "b", "kind": "new_element"}, answers)
    assert ok({"check": "option_kind", "column": "b", "kind": "new_element", "key": "best_eps"}, answers)
    assert ok({"check": "option_kind", "column": "c", "kind": "neither"}, answers)
    assert not ok({"check": "option_kind", "column": "c", "kind": "same_as"}, answers)


def test_a_check_with_a_missing_or_unknown_argument_is_refused():
    with pytest.raises(ValueError):
        ev.validate_check({"check": "equals", "path": "x"})
    with pytest.raises(ValueError):
        ev.validate_check({"check": "equals", "path": "x", "value": 1, "extra": 2})
    with pytest.raises(ValueError):
        ev.validate_check({"check": "similar_to", "path": "x", "value": "y"})


# ---------------------------------------------------------------- a run on the mock


def run(db_session, tmp_path, **more):
    end_read(db_session)
    provider = MockProvider()
    options = {"agents": ["intent", "profiler"], "runs": 1, "mappings": False} | more
    return provider, ev.run_evaluation(db_session, provider, LocalBlobStore(tmp_path / "blobs"), **options)


def test_a_run_logs_one_call_per_case_and_makes_no_proposal(db_session, tmp_path):
    provider, report = run(db_session, tmp_path, agents=["intent", "profiler", "table_extractor"])
    names = db_session.scalars(select(ProposalLogEntry.agent_name)).all()
    assert sorted(names) == sorted(["IntentEvaluator"] * 6 + ["ProfilerEvaluator"] * 6
                                   + ["TableExtractorEvaluator"] * 3)
    assert db_session.scalar(select(func.count()).select_from(ProposalRow)) == 0
    assert provider.requests_made == 15 == report.total_calls
    assert [(r.agent, r.cases, r.calls) for r in report.results] == [("intent", 6, 6), ("profiler", 6, 6),
                                                                    ("table_extractor", 3, 3)]
    assert all(r.passed + len({case for case, _ in r.failed}) == r.cases for r in report.results)


def test_the_mapping_evaluation_runs_with_the_same_number_of_runs(db_session, tmp_path):
    provider, report = run(db_session, tmp_path, agents=[], runs=2, mappings=True)
    mapping = [r for r in report.results if r.agent == "mapping"]
    assert [(r.run, r.cases, r.calls) for r in mapping] == [(1, 31, 4), (2, 31, 4)]
    assert provider.requests_made == 8 == report.total_calls
    assert ev.planned_calls([], runs=2, mappings=True) == 8


def test_two_mock_runs_give_identical_counts(db_session, tmp_path):
    _, report = run(db_session, tmp_path, agents=["intent", "glossary_builder", "transformation"], runs=2)
    by_agent = {}
    for r in report.results:
        by_agent.setdefault(r.agent, []).append((r.cases, r.passed, r.failed, r.calls))
    for counts in by_agent.values():
        assert counts[0] == counts[1]
    assert report.flags == []


def test_the_report_prints_counts_and_latency_and_no_model_answer(db_session, tmp_path):
    _, report = run(db_session, tmp_path, agents=ev.AGENT_ORDER)
    text = ev.format_report(report)
    assert "intent" in text and "mean latency" in text and "Total calls: " in text
    answers = db_session.scalars(select(ProposalLogEntry.output)).all()
    strings = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and len(node) >= 12:
            strings.append(node)

    for output in answers:
        walk(output)
    assert strings
    for value in strings:
        assert value not in text, "a model answer reached the report"


def test_a_flag_line_names_an_agent_whose_runs_differ_by_more_than_one_case():
    def result(run_no, passed):
        return ev.AgentResult(agent="intent", run=run_no, cases=6, passed=passed, failed=[], mean_latency_ms=1, calls=6)

    assert ev.flags_of([result(1, 6), result(2, 5)]) == []
    assert ev.flags_of([result(1, 6), result(2, 4)]) == ["FLAG intent: run 1 passed 6, run 2 passed 4"]


def test_the_json_file_holds_counts_only(db_session, tmp_path):
    _, report = run(db_session, tmp_path, agents=["intent"])
    data = ev.report_as_json(report)
    assert set(data) == {"results", "flags", "total_calls"}
    assert set(data["results"][0]) == {"agent", "run", "cases", "passed", "failed", "mean_latency_ms", "calls"}


# ---------------------------------------------------------------- the cap and the refusals


def _secrets(tmp_path, **values):
    from ports.secrets.env import EnvSecrets

    env = tmp_path / "eval.env"
    env.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return EnvSecrets(env)


APP = "postgresql+psycopg://amaze:pw@localhost:5432/amaze"
EVAL = "postgresql+psycopg://amaze:pw@localhost:5432/amaze_eval_not_used"


def test_the_planned_calls_are_cases_times_runs_plus_mapping_sources_times_runs():
    assert ev.planned_calls(["intent", "table_extractor"], runs=2, mappings=False) == 2 * 6 + 2 * 3
    total = sum(ev.REQUIRED_CASES[a] for a in ev.AGENT_ORDER)
    assert ev.planned_calls(ev.AGENT_ORDER, runs=2, mappings=True) == 2 * total + 2 * 4


def test_the_cap_refuses_before_any_call(tmp_path, capsys, monkeypatch):
    for key in ("DATABASE_URL", "EVAL_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    provider = MockProvider()
    secrets = _secrets(tmp_path, DATABASE_URL=APP, EVAL_DATABASE_URL=EVAL)
    code = ev.main(["--runs", "2", "--max-calls", "10"], secrets=secrets, provider=provider,
                   blob_store=LocalBlobStore(tmp_path / "b"))
    assert code == 2 and provider.requests_made == 0
    err = capsys.readouterr().err
    assert "--max-calls" in err and "10" in err


def test_an_unknown_agent_is_refused(tmp_path, capsys, monkeypatch):
    for key in ("DATABASE_URL", "EVAL_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    secrets = _secrets(tmp_path, DATABASE_URL=APP, EVAL_DATABASE_URL=EVAL)
    provider = MockProvider()
    assert ev.main(["--agents", "intent,nonsense"], secrets=secrets, provider=provider,
                   blob_store=LocalBlobStore(tmp_path / "b")) == 2
    assert provider.requests_made == 0 and "nonsense" in capsys.readouterr().err


@pytest.mark.parametrize("values", [
    {"DATABASE_URL": APP},  # EVAL_DATABASE_URL not set
    {"DATABASE_URL": APP, "EVAL_DATABASE_URL": APP},
    {"DATABASE_URL": APP, "EVAL_DATABASE_URL": "postgresql+psycopg://other:pw2@127.0.0.1/amaze"},  # same database
])
def test_the_script_refuses_without_its_own_database(tmp_path, monkeypatch, capsys, values):
    for key in ("DATABASE_URL", "EVAL_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    provider = MockProvider()
    assert ev.main([], secrets=_secrets(tmp_path, **values), provider=provider) == 2
    assert "EVAL_DATABASE_URL" in capsys.readouterr().err and provider.requests_made == 0


def test_the_providers_count_their_requests():
    provider = MockProvider()
    assert provider.requests_made == 0
    from ports.llm.azure_openai import AzureOpenAIProvider

    assert AzureOpenAIProvider.requests_made == 0  # the class default; an instance counts its own HTTP requests
