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
