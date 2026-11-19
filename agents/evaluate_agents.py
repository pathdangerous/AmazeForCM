"""python -m agents.evaluate_agents: score the LLM_PROVIDER's prompts on the gold sets (phase 7.8, S8).

    python -m agents.evaluate_agents [--agents a,b] [--runs 2] [--max-calls 200] [--mappings] [--json PATH]

Each agent prompt other than mapping has a gold set, fixtures/gold/<prompt_id>.json: `version`, `description` and
`cases`. A case is an `id`, an `input` (a valid instance of the agent's input schema, synthetic) and `checks`:
deterministic predicates on the structured output, from the fixed vocabulary CHECKS below. There is no free-text
similarity. The mapping prompt keeps fixtures/gold/mappings.json and agents/evaluate_mappings.py; `--mappings` runs
that evaluation here, the same number of runs.

Every call goes through Agent.run() with a read-only evaluator (approver_roles (), as MappingEvaluator): each is
logged in proposal_log, in the database named by EVAL_DATABASE_URL (never the application's: the same refusals as
evaluate_mappings), and no proposal is made. The evaluators have no run() of their own (the base class refuses it).

The report prints counts and latency only, never a model answer or a value from one: per agent and run the cases,
passes, failures, mean latency and requests made (the provider's `requests_made` counter, retries included), the
failed case ids with the name of the failed check, a FLAG line for an agent whose passes differ by more than one case
between two runs, and the total. The planned calls (cases x runs, plus the mapping sources x runs) are counted first;
above --max-calls nothing starts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from agents.base import Agent
from agents.evaluate_mappings import _same_database
from core.domain.go_live import ReviewSummaryInput, ReviewSummaryOutput
from core.domain.extraction import ExtractedTable, TableExtractionInput
from core.domain.mapping import GlossaryBuilderInput, GlossaryBuilderOutput
from core.domain.profiling import ProfilerInput, ProfilerOutput
from core.domain.rules import RuleRecommenderInput, RuleRecommenderOutput
from core.domain.sourcing import (AssistantInput, AssistantOutput, IntentInput, IntentOutput, SourceDiscoveryInput,
                                  SourceDiscoveryOutput)
from core.domain.transformation import TransformationInput, TransformationOutput
from core.repositories.tables import ProposalLogEntry
from ports.config import REPO_ROOT
from ports.llm.base import LLMRefused, LLMUnavailable
from ports.secrets import Secrets, get_secrets
from ports.secrets.base import SecretNotFound

GOLD_DIR = REPO_ROOT / "fixtures" / "gold"
DEFAULT_RUNS = 2
DEFAULT_MAX_CALLS = 200
MAPPING_SOURCES = 4  # the sources of fixtures/gold/mappings.json: one call each


# ---------------------------------------------------------------- the read-only evaluators, one per prompt


class IntentEvaluator(Agent):
    name = "IntentEvaluator"
    prompt_id = "intent"
    autonomy = "A2"
    approver_roles = ()
    input_schema = IntentInput
    output_schema = IntentOutput


class SourceDiscoveryEvaluator(Agent):
    name = "SourceDiscoveryEvaluator"
    prompt_id = "source_discovery"
    autonomy = "A2"
    approver_roles = ()
    input_schema = SourceDiscoveryInput
    output_schema = SourceDiscoveryOutput


class DiscoveryAssistantEvaluator(Agent):
    name = "DiscoveryAssistantEvaluator"
    prompt_id = "discovery_assistant"
    autonomy = "A2"
    approver_roles = ()
    input_schema = AssistantInput
    output_schema = AssistantOutput


class ProfilerEvaluator(Agent):
    name = "ProfilerEvaluator"
    prompt_id = "profiler"
    autonomy = "A2"
    approver_roles = ()
    input_schema = ProfilerInput
    output_schema = ProfilerOutput


class GlossaryBuilderEvaluator(Agent):
    name = "GlossaryBuilderEvaluator"
    prompt_id = "glossary_builder"
    autonomy = "A2"
    approver_roles = ()
    input_schema = GlossaryBuilderInput
    output_schema = GlossaryBuilderOutput


class TransformationEvaluator(Agent):
    name = "TransformationEvaluator"
    prompt_id = "transformation"
    autonomy = "A2"
    approver_roles = ()
    input_schema = TransformationInput
    output_schema = TransformationOutput


class RuleRecommenderEvaluator(Agent):
    name = "RuleRecommenderEvaluator"
    prompt_id = "rule_recommender"
    autonomy = "A2"
    approver_roles = ()
    input_schema = RuleRecommenderInput
    output_schema = RuleRecommenderOutput


class ReviewSummariserEvaluator(Agent):
    name = "ReviewSummariserEvaluator"
    prompt_id = "review_summariser"
    autonomy = "A1"
    approver_roles = ()
    input_schema = ReviewSummaryInput
    output_schema = ReviewSummaryOutput


class TableExtractorEvaluator(Agent):
    name = "TableExtractorEvaluator"
    prompt_id = "table_extractor"
    autonomy = "A2"
    approver_roles = ()
    input_schema = TableExtractionInput
    output_schema = ExtractedTable


AGENT_ORDER: list[str] = ["intent", "source_discovery", "discovery_assistant", "profiler", "glossary_builder",
                          "transformation", "rule_recommender", "review_summariser", "table_extractor"]
EVALUATORS: dict[str, type[Agent]] = {cls.prompt_id: cls for cls in (
    IntentEvaluator, SourceDiscoveryEvaluator, DiscoveryAssistantEvaluator, ProfilerEvaluator,
    GlossaryBuilderEvaluator, TransformationEvaluator, RuleRecommenderEvaluator, ReviewSummariserEvaluator,
    TableExtractorEvaluator)}
REQUIRED_CASES: dict[str, int] = {prompt_id: 6 for prompt_id in AGENT_ORDER} | {"table_extractor": 3}


# ---------------------------------------------------------------- the check vocabulary

_MISSING = object()


def _step(nodes: list, segment: str) -> list:
    out = []
    for node in nodes:
        if segment == "*":
            if isinstance(node, list):
                out.extend(node)
        elif isinstance(node, dict):
            out.append(node.get(segment, _MISSING))
        elif isinstance(node, list) and segment.isdigit() and int(segment) < len(node):
            out.append(node[int(segment)])
        else:
            out.append(_MISSING)
    return out


def resolve(output: Any, path: str) -> tuple[Any, bool]:
    """The value at a dotted path ('a.b.0.c'); a '*' segment fans out over a list. Returns (value, wild): with a
    wildcard the value is the list of what was found (an item without the rest of the path is left out)."""
    segments = path.split(".")
    nodes = [output]
    for segment in segments:
        nodes = _step(nodes, segment)
    if "*" in segments:
        return [n for n in nodes if n is not _MISSING], True
    return nodes[0], False


def _items(path: str, output: Any) -> list | object:
    value, wild = resolve(output, path)
    if wild:
        return value
    return _MISSING if value is _MISSING else [value]


def _equals(c, out):
    value, _ = resolve(out, c["path"])
    return value is not _MISSING and value == c["value"]


def _in(c, out):
    items = _items(c["path"], out)
    return items is not _MISSING and all(i in c["values"] for i in items)


def _contains(c, out):
    value, _ = resolve(out, c["path"])
    return value is not _MISSING and isinstance(value, (list, str)) and c["value"] in value


def _not_contains(c, out):
    value, _ = resolve(out, c["path"])
    return value is not _MISSING and isinstance(value, (list, str)) and c["value"] not in value


def _nonempty(c, out):
    value, wild = resolve(out, c["path"])
    if value is _MISSING:
        return False
    return bool(value) and (all(bool(i) for i in value) if wild else True)


def _length(c, out):
    value, _ = resolve(out, c["path"])
    return len(value) if isinstance(value, (list, str)) else None


def _count_at_least(c, out):
    n = _length(c, out)
    return n is not None and n >= c["n"]


def _count_at_most(c, out):
    n = _length(c, out)
    return n is not None and n <= c["n"]


def _numbers(c, out):
    items = _items(c["path"], out)
    if items is _MISSING or not items:
        return None
    return items if all(isinstance(i, (int, float)) and not isinstance(i, bool) for i in items) else None


def _number_at_most(c, out):
    numbers = _numbers(c, out)
    return numbers is not None and all(i <= c["value"] for i in numbers)


def _number_at_least(c, out):
    numbers = _numbers(c, out)
    return numbers is not None and all(i >= c["value"] for i in numbers)


def _strings(c, out):
    items = _items(c["path"], out)
    return items if items is not _MISSING and all(isinstance(i, str) for i in items) else None


def _matches(c, out):
    strings = _strings(c, out)
    return bool(strings) and all(re.search(c["pattern"], s, re.IGNORECASE) for s in strings)


def _not_matches(c, out):
    strings = _strings(c, out)
    return strings is not None and not any(re.search(c["pattern"], s, re.IGNORECASE) for s in strings)


def _column_steps(c, out) -> list | None:
    for column in out.get("columns", []):
        if column.get("source_field") == c["column"]:
            return column.get("steps", [])
    return None


_ARGUMENT = {"parse_date": "format", "cast": "type", "rename": "to"}


def _op_present(c, out):
    steps = _column_steps(c, out)
    for step in steps or []:
        if step.get("op") != c["op"]:
            continue
        if "arg" not in c:
            return True
        if c["op"] == "placeholder_to_null":
            return c["arg"] in step.get("values", [])
        return step.get(_ARGUMENT.get(c["op"], ""), _MISSING) == c["arg"]
    return False


def _op_absent(c, out):
    steps = _column_steps(c, out)
    return steps is not None and all(s.get("op") != c["op"] for s in steps)


def _no_steps(c, out):
    steps = _column_steps(c, out)
    return steps is not None and steps == []


def _option_kind(c, out):
    for answer in out.get("answers", []):
        if answer.get("column") != c["column"]:
            continue
        same_as, new = answer.get("same_as"), answer.get("new_element")
        kind = c["kind"]
        if kind == "same_as":
            return same_as is not None and c.get("key", same_as) == same_as
        if kind == "new_element":
            return new is not None and c.get("key", new.get("key")) == new.get("key")
        return kind == "neither" and same_as is None and new is None
    return False


# name -> (the function, required arguments, optional arguments)
CHECKS: dict[str, tuple[Callable[[dict, Any], bool], frozenset[str], frozenset[str]]] = {
    "equals": (_equals, frozenset({"path", "value"}), frozenset()),
    "in": (_in, frozenset({"path", "values"}), frozenset()),
    "contains": (_contains, frozenset({"path", "value"}), frozenset()),
    "not_contains": (_not_contains, frozenset({"path", "value"}), frozenset()),
    "nonempty": (_nonempty, frozenset({"path"}), frozenset()),
    "count_at_least": (_count_at_least, frozenset({"path", "n"}), frozenset()),
    "count_at_most": (_count_at_most, frozenset({"path", "n"}), frozenset()),
    "number_at_most": (_number_at_most, frozenset({"path", "value"}), frozenset()),
    "number_at_least": (_number_at_least, frozenset({"path", "value"}), frozenset()),
    "matches": (_matches, frozenset({"path", "pattern"}), frozenset()),
    "not_matches": (_not_matches, frozenset({"path", "pattern"}), frozenset()),
    "op_present": (_op_present, frozenset({"column", "op"}), frozenset({"arg"})),
    "op_absent": (_op_absent, frozenset({"column", "op"}), frozenset()),
    "no_steps": (_no_steps, frozenset({"column"}), frozenset()),
    "option_kind": (_option_kind, frozenset({"column", "kind"}), frozenset({"key"})),
}


def validate_check(check: dict) -> None:
    """Refuse a check that is not in the vocabulary, lacks an argument or carries one it does not take."""
    name = check.get("check")
    if name not in CHECKS:
        raise ValueError(f"unknown check {name!r}")
    _, required, optional = CHECKS[name]
    given = set(check) - {"check"}
    if not required <= given or not given <= (required | optional):
        raise ValueError(f"check {name!r} takes {sorted(required)} (and optionally {sorted(optional)}), got {sorted(given)}")


def check_passes(check: dict, output: Any) -> bool:
    return bool(CHECKS[check["check"]][0](check, output))


# ---------------------------------------------------------------- the gold sets and the plan


def load_gold(prompt_id: str, gold_dir: Path = GOLD_DIR) -> dict:
    return json.loads((gold_dir / f"{prompt_id}.json").read_text(encoding="utf-8"))


def planned_calls(agents: Sequence[str], *, runs: int, mappings: bool) -> int:
    """Cases x runs for each agent, plus the mapping sources x runs. Counted from the files, before any call."""
    cases = sum(len(load_gold(a)["cases"]) for a in agents)
    return runs * (cases + (MAPPING_SOURCES if mappings else 0))


# ---------------------------------------------------------------- a run


@dataclass(frozen=True)
class AgentResult:
    agent: str
    run: int
    cases: int
    passed: int
    failed: list[tuple[str, str]]  # (case id, the failed check's name); never a value
    mean_latency_ms: int | None
    calls: int


@dataclass
class Report:
    provider: str
    results: list[AgentResult] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    total_calls: int = 0


def _requests(provider) -> int:
    return int(getattr(provider, "requests_made", 0))


def _mean_latency(session: Session, log_ids: list) -> int | None:
    if not log_ids:
        return None
    rows = session.scalars(select(ProposalLogEntry.latency_ms).where(ProposalLogEntry.id.in_(log_ids))).all()
    session.commit()  # end the read: the next model call holds no connection
    return round(sum(rows) / len(rows)) if rows else None


def evaluate_agent(session: Session, provider, blob_store, prompt_id: str, run_no: int) -> AgentResult:
    """One Agent.run() per case; the case passes when every check on the output does."""
    evaluator = EVALUATORS[prompt_id](provider=provider, blob_store=blob_store)
    started = _requests(provider)
    cases = load_gold(prompt_id)["cases"]
    log_ids, failed, passed = [], [], 0
    for case in cases:
        agent_input = evaluator.input_schema.model_validate(case["input"])
        try:
            run = evaluator.run(session, agent_input)
        except (LLMUnavailable, LLMRefused) as exc:
            failed.append((case["id"], f"unavailable:{exc.reason}"))
            continue
        except Exception as exc:  # the class only, never its message (it could quote the input)
            failed.append((case["id"], f"error:{type(exc).__name__}"))
            continue
        log_ids.append(run.log_id)
        output = run.output.model_dump(mode="json")
        bad = [c["check"] for c in case["checks"] if not check_passes(c, output)]
        if bad:
            failed.extend((case["id"], name) for name in bad)
        else:
            passed += 1
    return AgentResult(agent=prompt_id, run=run_no, cases=len(cases), passed=passed, failed=failed,
                       mean_latency_ms=_mean_latency(session, log_ids), calls=_requests(provider) - started)


def evaluate_mapping_run(session: Session, provider, blob_store, run_no: int) -> AgentResult:
    from agents.evaluate_mappings import evaluate
    from agents.mapping import MappingEvaluator

    started = _requests(provider)
    result = evaluate(session, MappingEvaluator(provider=provider, blob_store=blob_store))
    score = result.score
    return AgentResult(agent="mapping", run=run_no, cases=score.total, passed=score.right,
                       failed=[(f"case {m.case}", "mapping") for m in score.misses],
                       mean_latency_ms=_mean_latency(session, result.log_ids), calls=_requests(provider) - started)


def flags_of(results: Sequence[AgentResult]) -> list[str]:
    """An agent whose passes differ by more than one case between two runs (run n and run n + 1)."""
    lines = []
    by_agent: dict[str, list[AgentResult]] = {}
    for r in sorted(results, key=lambda r: r.run):
        by_agent.setdefault(r.agent, []).append(r)
    for agent, rows in by_agent.items():
        for first, second in zip(rows, rows[1:], strict=False):
            if abs(first.passed - second.passed) > 1:
                lines.append(f"FLAG {agent}: run {first.run} passed {first.passed}, run {second.run} passed "
                             f"{second.passed}")
    return lines


def run_evaluation(session: Session, provider, blob_store, *, agents: Sequence[str], runs: int,
                   mappings: bool) -> Report:
    """Every run of every agent (and of the mapping evaluation with `mappings`); the session must be in no
    transaction (Agent.run refuses one)."""
    report = Report(provider=getattr(provider, "name", "unknown"))
    started = _requests(provider)
    for agent in agents:
        for run_no in range(1, runs + 1):
            report.results.append(evaluate_agent(session, provider, blob_store, agent, run_no))
    if mappings:
        for run_no in range(1, runs + 1):
            report.results.append(evaluate_mapping_run(session, provider, blob_store, run_no))
    report.flags = flags_of(report.results)
    report.total_calls = _requests(provider) - started
    return report


# ---------------------------------------------------------------- the report


def format_report(report: Report) -> str:
    lines = [f"Agent evaluation: provider {report.provider}", "",
             f"{'agent':<22}{'run':>4}{'cases':>7}{'passed':>8}{'failed':>8}{'mean latency ms':>17}{'calls':>7}"]
    for r in report.results:
        latency = "-" if r.mean_latency_ms is None else str(r.mean_latency_ms)
        lines.append(f"{r.agent:<22}{r.run:>4}{r.cases:>7}{r.passed:>8}{r.cases - r.passed:>8}{latency:>17}"
                     f"{r.calls:>7}")
    failures = [r for r in report.results if r.failed]
    if failures:
        lines.append("")
        lines += [f"Failed {r.agent} run {r.run}: " + ", ".join(f"{case} ({check})" for case, check in r.failed)
                  for r in failures]
    if report.flags:
        lines += ["", *report.flags]
    lines += ["", f"Total calls: {report.total_calls}"]
    return "\n".join(lines)


def report_as_json(report: Report) -> dict:
    return {"results": [{"agent": r.agent, "run": r.run, "cases": r.cases, "passed": r.passed,
                         "failed": [{"case": case, "check": check} for case, check in r.failed],
                         "mean_latency_ms": r.mean_latency_ms, "calls": r.calls} for r in report.results],
            "flags": report.flags, "total_calls": report.total_calls}


# ---------------------------------------------------------------- the command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agents.evaluate_agents", description=__doc__.split("\n\n")[0])
    parser.add_argument("--agents", default=",".join(AGENT_ORDER), help="comma-separated prompt ids (default: all)")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    parser.add_argument("--mappings", action="store_true", help="also run the mapping evaluation, the same runs")
    parser.add_argument("--json", dest="json_path", default=None, help="write the counts (no answers) here")
    return parser


def main(argv: Sequence[str] | None = None, *, secrets: Secrets | None = None, provider=None,
         blob_store=None) -> int:
    args = _parser().parse_args(argv)
    secrets = secrets or get_secrets()
    try:
        eval_url = secrets.get("EVAL_DATABASE_URL")
    except SecretNotFound:
        print("EVAL_DATABASE_URL is not set: the evaluation logs to its own database (README: amaze_eval).",
              file=sys.stderr)
        return 2
    try:
        app_url = secrets.get("DATABASE_URL")
    except SecretNotFound:
        app_url = None
    if app_url is not None and _same_database(eval_url, app_url):
        print("EVAL_DATABASE_URL names the application database (DATABASE_URL): refused. Point it at amaze_eval.",
              file=sys.stderr)
        return 2
    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    unknown = [a for a in agents if a not in EVALUATORS]
    if unknown or args.runs < 1:
        print("Unknown agent(s): " + ", ".join(unknown) + ". Known: " + ", ".join(AGENT_ORDER) if unknown
              else "--runs must be at least 1", file=sys.stderr)
        return 2
    planned = planned_calls(agents, runs=args.runs, mappings=args.mappings)
    if planned > args.max_calls:
        print(f"Planned calls {planned} are above --max-calls {args.max_calls}: nothing was started.", file=sys.stderr)
        return 2
    from sqlalchemy import create_engine

    from ports.config import get_settings
    from ports.llm import get_llm_provider
    from ports.storage import get_blob_store

    settings = get_settings()
    provider = provider or get_llm_provider(settings)
    blob_store = blob_store or get_blob_store(settings)
    print(f"Planned calls: {planned} (cap {args.max_calls}); provider {getattr(provider, 'name', 'unknown')}")
    engine = create_engine(eval_url)
    try:
        with Session(engine) as session:
            report = run_evaluation(session, provider, blob_store, agents=agents, runs=args.runs,
                                    mappings=args.mappings)
    finally:
        engine.dispose()
    print(format_report(report))
    print(f"Logged to database {make_url(eval_url).database}.")
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report_as_json(report), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
