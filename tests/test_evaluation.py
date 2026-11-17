"""agents/evaluation.py (phase 5 plan, assumptions 36 to 38): the pure score of answers against the gold set.

Task 3 tests the score only; task 9 adds the evaluator, the script and the mock's pinned score.
"""

from __future__ import annotations

import pytest

from agents.evaluation import GoldCase, score


def case(n, outcome, element=None, must_offer=(), category="easy"):
    return GoldCase(case=n, source="syn_gold_x", column=f"c{n}", type="VARCHAR", hint=None, category=category,
                    outcome=outcome, element=element, must_offer=tuple(must_offer))


CASES = [
    case(1, "mapped", "isin"),
    case(2, "mapped", "currency", category="misleading name"),
    case(3, "tie_to_steward", must_offer=("issue_date", "maturity_date"), category="tie"),
    case(4, "tie_to_steward", must_offer=("close_price", "mid_price"), category="tie"),
    case(5, "no_element_back_to_05", category="no element"),
    case(6, "no_element_back_to_05", category="no element"),
]


def test_a_mapped_case_is_right_only_with_exactly_its_element():
    s = score(CASES[:2], {1: ["isin"], 2: ["currency", "price_currency"]}, {1: ["isin"], 2: ["currency"]})
    assert (s.right, s.total) == (1, 2)


def test_a_tie_is_right_with_two_or_three_that_hold_every_must_offer_element():
    answers = {3: ["issue_date", "maturity_date", "price_date"], 4: ["close_price"]}
    assert score(CASES[2:4], answers, {}).right == 1
    four = {3: ["issue_date", "maturity_date", "price_date", "lot_size"], 4: ["close_price", "open_price"]}
    assert score(CASES[2:4], four, {}).right == 0  # more than 3; a must-offer element missing


def test_a_no_element_case_is_right_with_nothing_named():
    assert score(CASES[4:], {5: [], 6: ["isin"]}, {}).right == 1


def test_a_case_with_no_answer_counts_as_nothing_named():
    assert score(CASES[4:5], {}, {}).right == 1


def test_wrong_maps_are_one_element_the_gold_set_does_not_accept():
    answers = {1: ["cusip"], 2: ["currency"], 3: ["issue_date"], 4: ["isin"], 5: ["isin"], 6: ["a", "b"]}
    s = score(CASES, answers, {})
    assert [(w.case, w.named) for w in s.wrong_maps] == [(1, "cusip"), (4, "isin"), (5, "isin")]
    assert [m.case for m in s.misses] == [1, 3, 4, 5, 6]


def test_the_count_per_category():
    answers = {1: ["isin"], 2: ["isin"], 3: ["issue_date", "maturity_date"], 4: [], 5: [], 6: []}
    assert score(CASES, answers, {}).per_category == {"easy": (1, 1), "misleading name": (0, 1), "tie": (1, 2),
                                                      "no element": (2, 2)}


def test_the_mean_tie_size_is_over_every_answer_of_two_or_more():
    answers = {1: ["isin"], 3: ["issue_date", "maturity_date"], 4: ["close_price", "mid_price", "open_price",
                                                                    "high_price"], 6: ["a", "b", "c"]}
    assert score(CASES, answers, {}).mean_tie_size == pytest.approx(3.0)
    assert score(CASES, {1: ["isin"]}, {}).mean_tie_size is None


def test_candidate_recall_counts_each_expected_or_must_offer_element_in_the_list_code_built():
    candidates = {1: ["isin", "cusip"], 2: ["price_currency"], 3: ["issue_date"], 4: ["close_price", "mid_price"]}
    s = score(CASES, {}, candidates)
    assert (s.recall_found, s.recall_total) == (4, 6)
    assert s.recall_missing == [(2, "currency"), (3, "maturity_date")]


def test_the_score_names_the_fraction():
    s = score(CASES[:2], {1: ["isin"]}, {})
    assert s.fraction == pytest.approx(0.5)


# ---------------------------------------------------------------- task 9: the evaluator on the gold set


@pytest.fixture
def evaluated(db_session, tmp_path):
    from agents.evaluate_mappings import evaluate
    from agents.mapping import MappingEvaluator
    from ports.llm.mock import MockProvider
    from ports.storage.local import LocalBlobStore
    from tests.step_chains import end_read

    end_read(db_session)
    return evaluate(db_session, MappingEvaluator(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "b")))


def test_the_mock_scores_as_measured_and_misses_these(evaluated):
    """Phase 5 plan, assumption 38: the prototype predicted 25 of 31 under the approved rule; pinned here."""
    s = evaluated.score
    assert (s.right, s.total) == (25, 31)
    assert [(m.case, m.column) for m in s.misses] == [(8, "ccy"), (14, "price"), (15, "quote_ccy"),
                                                     (16, "instrument"), (17, "trade_date"), (19, "high")]
    assert [(w.case, w.column, w.named) for w in s.wrong_maps] == [(15, "quote_ccy", "currency"),
                                                                   (19, "high", "high_price")]


def test_the_mocks_tie_size_and_candidate_recall_are_pinned(evaluated):
    """The baseline phase 8 compares Azure OpenAI with (plan, section 16, note 11; review finding 6)."""
    s = evaluated.score
    assert s.mean_tie_size == pytest.approx(5.0)
    assert s.recall_total == 22 + 2 * 3  # one element per mapped case, two must-offer elements per tie
    assert (s.recall_found, s.recall_missing) == (27, [(8, "price_currency")])  # never a candidate of ccy


def test_an_evaluation_creates_no_proposal_and_logs_every_call(db_session, evaluated):
    from sqlalchemy import func, select

    from core.repositories.tables import ProposalLogEntry, ProposalRow

    names = db_session.scalars(select(ProposalLogEntry.agent_name)).all()
    assert names == ["MappingEvaluator"] * 4  # one call per gold source
    assert db_session.scalar(select(func.count()).select_from(ProposalRow)) == 0


def test_the_report_prints_score_tie_size_recall_and_wrong_maps(evaluated):
    from agents.evaluate_mappings import format_report

    text = format_report(evaluated, provider="mock")
    for line in ("Score: 25 of 31 (80.6%)", "Mean tie size:", "Candidate recall:", "Wrong maps: 2"):
        assert line in text, line
    assert "15 quote_ccy -> currency" in text and "19 high -> high_price" in text


def _secrets(tmp_path, **values):
    from ports.secrets.env import EnvSecrets

    env = tmp_path / "eval.env"
    env.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return EnvSecrets(env)


APP = "postgresql+psycopg://amaze:pw@localhost:5432/amaze"


@pytest.mark.parametrize("values", [
    {"DATABASE_URL": APP},  # EVAL_DATABASE_URL not set
    {"DATABASE_URL": APP, "EVAL_DATABASE_URL": APP},
    {"DATABASE_URL": APP, "EVAL_DATABASE_URL": "postgresql+psycopg://other:pw2@127.0.0.1/amaze"},  # same database
])
def test_the_script_refuses_without_its_own_database(tmp_path, monkeypatch, capsys, values):
    from agents.evaluate_mappings import main

    for key in ("DATABASE_URL", "EVAL_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    assert main([], secrets=_secrets(tmp_path, **values)) == 2
    assert "EVAL_DATABASE_URL" in capsys.readouterr().err
