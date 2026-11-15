"""MockProvider rules (phase 3 plan, assumption 25): pure functions over the agent's input.

Each rule takes the JSON block that ends the user message and returns the output as plain data. No
clock, no randomness, no file or database access; the same input always gives the same output.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from ports.llm import mock_rules
from ports.llm.mock_rules import RULES

CHANNELS = [{"channel": "exchanges", "visibility": "public"},
            {"channel": "market_data_vendors", "visibility": "public"},
            {"channel": "internal_systems", "visibility": "private"}]


def _intent(text="open price", purpose=None, note=None):
    return {"requirement_text": text, "purpose": purpose, "note": note,
            "elements": [{"key": "open_price", "name": "Open price"}], "terms": [], "channels": CHANNELS}


def test_rules_are_named_by_prompt_id():
    assert set(RULES) == {"intent", "source_discovery", "discovery_assistant", "profiler"}


# ---------------------------------------------------------------- profiler (phase 4 plan, assumption 15)


def _item(item, *, declared=True, in_file=True, null_rate=0.0):
    return {"item": item, "kind": "element", "declared": declared, "column": item if declared else None,
            "in_file": in_file, "null_rate": null_rate if in_file else None, "distinct_count": 1 if in_file else None}


def _profiler(*items, outcome="profiled", rows=10, detail=None, requirements=1):
    coverage = [{"requirement_id": f"00000000-0000-0000-0000-00000000000{i}", "items": list(items),
                 "missing": [x["item"] for x in items if not x["declared"] or x["in_file"] is False]}
                for i in range(1, requirements + 1)]
    return {"outcome": outcome, "row_count": rows, "detail": detail, "coverage": coverage}


@pytest.mark.parametrize(("data", "outcome", "gaps", "reasons"), [
    (_profiler(_item("volume")), "meets", [], []),
    (_profiler(_item("volume"), _item("isin", declared=False, in_file=False)), "meets_with_gaps",
     ["isin is not in the file"], []),
    (_profiler(_item("issue_date", null_rate=0.2)), "meets_with_gaps", ["issue_date is empty in 2 of 10 rows"], []),
    (_profiler(_item("isin", declared=False, in_file=False)), "rejected", [], ["No requested item is in the file"]),
    (_profiler(_item("lot_size", in_file=None), outcome="spec_only_low_confidence", rows=0,
               detail="The file has a header and no data rows."),
     "meets_with_gaps", ["No data rows were profiled: The file has a header and no data rows."], []),
    (_profiler(_item("isin", declared=False, in_file=None), outcome="spec_only_low_confidence", rows=0,
               detail="Not a table."), "rejected", [], ["The source declares none of the requested items"]),
    (_profiler(_item("volume"), _item("isin", declared=False, in_file=False), requirements=2), "meets_with_gaps",
     ["isin is not in the file"], []),  # a gap of two requirements is listed once
])
def test_the_profiler_rule(data, outcome, gaps, reasons):
    out = RULES["profiler"](data)
    assert (out["recommended_outcome"], out["gaps"], out["reasons"]) == (outcome, gaps, reasons)
    assert out["summary"]


# ---------------------------------------------------------------- intent


def test_intent_is_public_by_default_and_searches_the_public_channels():
    out = RULES["intent"](_intent())
    assert out["visibility"] == "public"
    assert out["where_to_search"] == ["exchanges", "market_data_vendors"]
    assert out["provider"] and out["reason"]


@pytest.mark.parametrize("field", ["text", "purpose", "note"])
@pytest.mark.parametrize("word", ["internal", "private", "in-house", "proprietary"])
def test_intent_is_private_when_a_private_word_appears(field, word):
    out = RULES["intent"](_intent(**{field: f"For {word.upper()} desk use"}))
    assert out["visibility"] == "private"
    assert out["where_to_search"] == ["internal_systems"]


def test_intent_matches_whole_words_only():
    assert RULES["intent"](_intent(purpose="international markets"))["visibility"] == "public"


# ---------------------------------------------------------------- source_discovery


def _candidate(code, elements, other_fields=()):
    return {"code": code, "name": code, "elements": list(elements), "other_fields": list(other_fields)}


def test_source_discovery_ranks_most_covered_first_then_fewest_extra_fields_then_code():
    data = {"requested_elements": ["open_price", "close_price"], "requested_terms": [],
            "candidates": [_candidate("syn_c", ["open_price"]),
                           _candidate("syn_b", ["open_price", "close_price", "volume", "high_price"]),
                           _candidate("syn_a", ["open_price", "close_price", "volume", "low_price"]),
                           _candidate("syn_d", ["open_price", "close_price"])]}
    ranked = [r["source_code"] for r in RULES["source_discovery"](data)["ranked"]]
    assert ranked == ["syn_d", "syn_a", "syn_b", "syn_c"]


def test_source_discovery_counts_a_term_that_equals_an_other_field():
    data = {"requested_elements": ["instrument_id"], "requested_terms": ["ESG  Score"],
            "candidates": [_candidate("syn_x", ["instrument_id"], ["esg score"]),
                           _candidate("syn_y", ["instrument_id"])]}
    ranked = RULES["source_discovery"](data)["ranked"]
    assert [r["source_code"] for r in ranked] == ["syn_x", "syn_y"]
    assert "2 of 2" in ranked[0]["reason"]


def test_source_discovery_leaves_out_a_source_that_covers_nothing():
    data = {"requested_elements": ["open_price"], "requested_terms": [],
            "candidates": [_candidate("syn_x", ["volume"])]}
    assert RULES["source_discovery"](data)["ranked"] == []


# ---------------------------------------------------------------- discovery_assistant

GLOSSARY = [{"key": "close_price", "name": "Close price", "aliases": ["closing_price", "close"]},
            {"key": "open_price", "name": "Open price", "aliases": ["opening_price", "open"]},
            {"key": "maturity_date", "name": "Maturity date", "aliases": []}]


def _assist(*terms):
    return RULES["discovery_assistant"]({"query": " ".join(terms), "unmatched_terms": list(terms),
                                         "glossary": GLOSSARY})["suggestions"]


def test_closing_prices_suggests_close_price():
    assert [(s["term"], s["element_key"]) for s in _assist("closing prices")] == [("closing prices", "close_price")]


def test_a_near_spelling_above_the_threshold_is_suggested():
    assert [s["element_key"] for s in _assist("maturity dat")] == ["maturity_date"]


def test_a_word_like_nothing_gets_no_suggestion():
    assert _assist("weather forecast") == []


# ---------------------------------------------------------------- purity


@pytest.mark.parametrize("prompt_id, data", [
    ("intent", _intent(note="internal")),
    ("source_discovery", {"requested_elements": ["open_price"], "requested_terms": [],
                          "candidates": [_candidate("syn_x", ["open_price"])]}),
    ("discovery_assistant", {"query": "closing prices", "unmatched_terms": ["closing prices"], "glossary": GLOSSARY}),
])
def test_a_rule_is_deterministic_and_does_not_change_its_input(prompt_id, data):
    before = copy.deepcopy(data)
    assert RULES[prompt_id](data) == RULES[prompt_id](copy.deepcopy(data))
    assert data == before


def test_the_rules_module_imports_no_clock_randomness_files_or_database():
    tree = ast.parse(Path(mock_rules.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "re", "difflib", "typing", "collections"}, imported
    assert "open(" not in Path(mock_rules.__file__).read_text(encoding="utf-8")
