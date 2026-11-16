"""python -m agents.evaluate_mappings: score the LLM_PROVIDER's MappingAgent prompt on the gold set
(phase 5 plan, assumptions 35 to 38; section 8.1).

The MappingEvaluator (read only, the mapping prompt, input and output) answers each gold source once,
through Agent.run(), so every call is logged in proposal_log and no proposal is made. Each column gets the
candidate list step 06 builds (candidate_list with the spec's hint, against the 33 seed elements), and
code keeps the agent's keys exactly as step 06 does (kept_elements). agents/evaluation.score() scores them.

The calls are logged to a separate database named by EVAL_DATABASE_URL (read through the Secrets port, as
DATABASE_URL is: it holds a password), never to the application's. The script refuses, with exit code 2,
when EVAL_DATABASE_URL is not set or names the same database as DATABASE_URL. README.md shows how to
create amaze_eval and bring it to the migration head.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from agents.evaluation import Gold, Score, load_gold, score
from agents.mapping import MappingEvaluator
from core.domain.glossary import GlossaryElementDraft
from core.domain.mapping import ColumnToMap, MappingInput
from core.seed import load_seed_elements
from core.steps.candidates import candidate_list, kept_elements
from ports.secrets import Secrets, get_secrets
from ports.secrets.base import SecretNotFound

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", None, ""}


@dataclass(frozen=True)
class Evaluation:
    gold: Gold
    score: Score
    answers: dict[int, list[str]]
    candidates: dict[int, list[str]]
    log_ids: list


def seed_glossary() -> list[GlossaryElementDraft]:
    fields = ("key", "name", "domain", "data_type", "definition", "aliases")
    return [GlossaryElementDraft(**{k: e[k] for k in fields}) for e in load_seed_elements()]


def evaluate(session: Session, agent: MappingEvaluator, gold: Gold | None = None) -> Evaluation:
    """One Agent.run() per gold source; the session must not be in a transaction (Agent.run refuses)."""
    gold = gold or load_gold()
    glossary = seed_glossary()
    answers, candidates, log_ids = {}, {}, []
    for source in gold.sources:
        cases = [c for c in gold.cases if c.source == source.code]
        columns = [ColumnToMap(name=c.column, type=c.type, candidates=candidate_list(c.column, c.type, glossary,
                                                                                     hint=c.hint)) for c in cases]
        run = agent.run(session, MappingInput(feed_id=None, source_code=source.code, source_name=source.name,
                                              source_description=source.description, columns=columns))
        session.commit()
        log_ids.append(run.log_id)
        said = {}
        for answer in run.output.answers:
            said.setdefault(answer.column, answer.elements)
        for case, column in zip(cases, columns, strict=True):
            answers[case.case] = kept_elements(column, said.get(column.name, []))
            candidates[case.case] = [x.key for x in column.candidates]
    return Evaluation(gold=gold, score=score(gold.cases, answers, candidates), answers=answers,
                      candidates=candidates, log_ids=log_ids)


def _expected(case) -> str:
    if case.outcome == "mapped":
        return case.element
    if case.outcome == "tie_to_steward":
        return "tie: " + ", ".join(case.must_offer)
    return "no element"


def format_report(evaluation: Evaluation, *, provider: str) -> str:
    s = evaluation.score
    missed = {m.case for m in s.misses}
    lines = [f"Mapping evaluation: provider {provider}, prompt mapping, gold set version {evaluation.gold.version}",
             "", f"{'case':>4}  {'column':<14} {'category':<16} {'expected':<34} {'named':<40} right"]
    for case in evaluation.gold.cases:
        named = ", ".join(evaluation.answers.get(case.case, [])) or "-"
        lines.append(f"{case.case:>4}  {case.column:<14} {case.category:<16} {_expected(case):<34} {named:<40} "
                     f"{'no' if case.case in missed else 'yes'}")
    tie = f"{s.mean_tie_size:.1f}" if s.mean_tie_size is not None else "none"
    lines += ["", f"Score: {s.right} of {s.total} ({100 * s.fraction:.1f}%)",
              "By category: " + ", ".join(f"{c} {r}/{n}" for c, (r, n) in s.per_category.items()),
              f"Mean tie size: {tie} (over every answer naming two or more elements)",
              f"Candidate recall: {s.recall_found} of {s.recall_total}"
              + ("; not offered: " + ", ".join(f"case {c} {e}" for c, e in s.recall_missing) if s.recall_missing
                 else ""),
              f"Wrong maps: {len(s.wrong_maps)}" + "".join(f"\n  {w.case} {w.column} -> {w.named}" for w in s.wrong_maps)]
    return "\n".join(lines)


def _same_database(a: str, b: str) -> bool:
    x, y = make_url(a), make_url(b)
    host = (lambda u: "localhost" if u.host in LOCAL_HOSTS else u.host)
    return (host(x), x.port or 5432, x.database) == (host(y), y.port or 5432, y.database)


def main(argv: Sequence[str] | None = None, *, secrets: Secrets | None = None) -> int:
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
    from ports.config import get_settings
    from ports.llm import get_llm_provider
    from ports.storage import get_blob_store

    settings = get_settings()
    provider = get_llm_provider(settings)
    engine = create_engine(eval_url)
    try:
        with Session(engine) as session:
            result = evaluate(session, MappingEvaluator(provider=provider, blob_store=get_blob_store(settings)))
    finally:
        engine.dispose()
    print(format_report(result, provider=provider.name))
    print(f"\nLogged {len(result.log_ids)} calls to database {make_url(eval_url).database}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
