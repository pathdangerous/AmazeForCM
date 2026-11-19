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
    assert set(RULES) == {"intent", "source_discovery", "discovery_assistant", "profiler", "glossary_builder",
                          "mapping", "rule_recommender", "review_summariser",
                          "transformation"}  # phase 7.8, task 4.3 (old: without "transformation")


def test_the_rule_recommender_rule_makes_one_rule_per_offer_and_leaves_the_checks_to_code():
    """Phase 6 plan, assumption 13: one rule per template offered, the SQL as the template wrote it."""
    offer = {"template": "present", "rule_key": "volume_present", "description": "Volume is present.",
             "sql": "SELECT _row FROM curated WHERE \"volume\" IS NULL", "element_keys": ["volume"]}
    key_offer = {"template": "one_row_per_key", "rule_key": "one_row_per_isin", "description": "One row per isin.",
                 "sql": "SELECT _row FROM curated", "element_keys": ["isin"]}
    data = {"elements": [{"key": "volume", "templates": [offer]}, {"key": "isin", "templates": []}],
            "feed_templates": [key_offer]}
    assert RULES["rule_recommender"](data) == {"rules": [
        {"rule_key": "volume_present", "description": "Volume is present.", "sql": offer["sql"],
         "element_keys": ["volume"], "template": "present"},
        {"rule_key": "one_row_per_isin", "description": "One row per isin.", "sql": "SELECT _row FROM curated",
         "element_keys": ["isin"], "template": "one_row_per_key"}]}
    assert RULES["rule_recommender"]({"elements": [], "feed_templates": []}) == {"rules": []}


# ---------------------------------------------------------------- phase 5: glossary_builder and mapping


def _cand(key, score, *, fits=True, hint=False, data_type="string"):
    return {"key": key, "name": key.replace("_", " ").capitalize(), "data_type": data_type, "definition": "d",
            "aliases": [], "score": score, "from_hint": hint, "type_fits": fits}


def _builder(*columns):
    return {"source_name": "Bond terms", "source_domain": "security_master", "domains": ["pricing", "security_master"],
            "columns": [{"name": n, "type": t, "candidates": c} for n, t, c in columns]}


def test_the_glossary_builder_rule():
    data = _builder(("venue_mic_code", "VARCHAR", [_cand("exchange_mic", 0.67), _cand("ticker", 0.4)]),
                    ("date", "DATE", [_cand("issue_date", 0.5), _cand("price_date", 0.5)]),
                    ("score_year", "BIGINT", [_cand("price_date", 0.33, fits=False)]),
                    ("maturity", "VARCHAR", [_cand("maturity_date", 1.0, fits=False)]),
                    ("Open Price", "DOUBLE", []))
    answers = {a["column"]: a for a in RULES["glossary_builder"](data)["answers"]}
    assert answers["venue_mic_code"]["same_as"] == "exchange_mic" and answers["venue_mic_code"]["new_element"] is None
    assert (answers["date"]["same_as"], answers["date"]["new_element"]) == (None, None)  # several fit: step 06
    assert answers["score_year"]["new_element"] == {
        "key": "score_year", "name": "Score year", "domain": "security_master", "data_type": "integer",
        "definition": "Score year: column score_year of Bond terms. To be confirmed by the glossary owner.",
        "aliases": []}
    assert answers["maturity"]["new_element"]["key"] == "maturity"  # its one candidate's type does not fit
    assert (answers["Open Price"]["new_element"]["key"], answers["Open Price"]["new_element"]["data_type"]) == (
        "open_price", "decimal")


@pytest.mark.parametrize(("duckdb", "data_type"), [("VARCHAR", "string"), ("BIGINT", "integer"),
                                                   ("DOUBLE", "decimal"), ("DECIMAL(18,3)", "decimal"),
                                                   ("DATE", "date"), ("TIMESTAMP", "datetime"),
                                                   ("BOOLEAN", "boolean"), (None, "string"), ("BLOB", "string")])
def test_the_glossary_builder_types_a_new_element_from_the_duckdb_family(duckdb, data_type):
    [answer] = RULES["glossary_builder"](_builder(("col_x", duckdb, [])))["answers"]
    assert answer["new_element"]["data_type"] == data_type


def test_the_glossary_builder_rule_names_the_security_master_columns_in_plain_words():
    """Phase 7.8, task 6.1 (assumption 41): estimates in their own domain, names and definitions built from the column,
    the type from the profile (an estimate is a decimal even when the file holds text), and never a unit."""
    cols = [("BEST_EPS", "DOUBLE"), ("BEST_BPS", "DOUBLE"), ("BEST_EBITDA", "DOUBLE"), ("BEST_CAPEX", "DOUBLE"),
            ("BEST_EV_TO_BEST_EBITDA", "DOUBLE"), ("BEST_ESP_GAAP", "DOUBLE"), ("BEST_OPP", "DOUBLE"),
            ("BEST_EPS_6_MONTH_PERCENT_CHANGE", "DOUBLE"), ("BEST_EPS_1_WEEK_PERCENT_CHANGE", "VARCHAR"),
            ("SECTOR", "VARCHAR"), ("F_PERIOD", "VARCHAR"), ("RECORD_DATE", "VARCHAR"), ("SOURCE", "VARCHAR"),
            ("SHORTNAME", "VARCHAR")]
    data = _builder(*[(n, t, [_cand("price_source", 0.5)] if n == "SOURCE" else []) for n, t in cols])
    data["domains"] = ["pricing", "security_master"]
    data["source_domain"] = "pricing"  # the first domain when the spec names none
    answers = {a["column"]: a["new_element"] for a in RULES["glossary_builder"](data)["answers"]}
    assert {a["column"] for a in RULES["glossary_builder"](data)["answers"]} == {n for n, _ in cols}
    names = {c: answers[c]["name"] for c, _ in cols}
    assert names["BEST_EPS"] == "Best earnings per share estimate"
    assert names["BEST_BPS"] == "Best book value per share estimate"
    assert names["BEST_EBITDA"] == "Best EBITDA estimate" and names["BEST_CAPEX"] == "Best capital expenditure estimate"
    assert names["BEST_EV_TO_BEST_EBITDA"] == "Best enterprise value to EBITDA estimate"
    assert names["BEST_ESP_GAAP"] == "Best GAAP earnings per share estimate"
    assert names["BEST_OPP"] == "Best operating profit estimate"
    assert names["BEST_EPS_6_MONTH_PERCENT_CHANGE"] == "Best earnings per share estimate, change over 6 months"
    assert names["BEST_EPS_1_WEEK_PERCENT_CHANGE"] == "Best earnings per share estimate, change over 1 week"
    for column in (c for c, _ in cols if c.startswith("BEST_")):
        element = answers[column]
        assert (element["key"], element["domain"], element["data_type"], element["unit"]) == (
            column.lower(), "estimates", "decimal", None)
        assert element["definition"].startswith("Consensus estimate of ") or element["definition"].startswith(
            "Change in the consensus estimate of ")
        assert "Bond terms" in element["definition"] and column in element["definition"]
    assert [(answers[c]["key"], answers[c]["name"], answers[c]["data_type"], answers[c]["domain"], answers[c]["unit"])
            for c in ("SECTOR", "F_PERIOD", "RECORD_DATE", "SOURCE", "SHORTNAME")] == [
        ("sector", "Sector", "string", "security_master", None),
        ("fiscal_period", "Fiscal period", "string", "security_master", None),
        ("record_date", "Record date", "date", "security_master", None),
        ("data_source", "Data source", "string", "security_master", None),
        ("short_name", "Short name", "string", "security_master", None)]
    assert answers["F_PERIOD"]["aliases"] == ["f_period"] and answers["SOURCE"]["aliases"] == ["source"]


def test_a_column_that_is_exactly_an_element_is_not_renamed_by_the_security_master_names():
    [answer] = RULES["glossary_builder"](_builder(("sector", "VARCHAR", [_cand("sector", 1.0)])))["answers"]
    assert answer["same_as"] == "sector" and answer["new_element"] is None


def _mapping(*columns):
    return {"source_name": "Bond terms", "columns": [{"name": n, "type": "VARCHAR", "candidates": c}
                                                    for n, c in columns]}


def test_the_mapping_rule():
    data = _mapping(("isin", [_cand("isin", 1.0), _cand("isin_code_x", 0.5)]),
                    ("instr_id", [_cand("figi", 0.33), _cand("instrument_id", 0.33, hint=True)]),
                    ("date", [_cand("issue_date", 0.5), _cand("maturity_date", 0.5), _cand("as_of", 0.33)]),
                    ("lot", [_cand("lot_size", 0.5, fits=False, hint=True), _cand("trading_lot_x", 0.4)]),
                    ("maturity", [_cand("maturity_date", 1.0, fits=False)]),
                    ("ccy", [_cand("currency", 1.0), _cand("trade_currency", 1.0)]))
    answers = {a["column"]: a["elements"] for a in RULES["mapping"](data)["answers"]}
    assert answers == {"isin": ["isin"], "instr_id": ["instrument_id"], "date": ["issue_date", "maturity_date"],
                       "lot": [], "maturity": [], "ccy": ["currency", "trade_currency"]}


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


@pytest.mark.parametrize(("data", "summary"), [
    (_profiler(_item("volume")), "10 rows; 1 of 1 requested items is in the file."),
    (_profiler(_item("volume"), _item("isin", declared=False, in_file=False)),
     "10 rows; 1 of 2 requested items is in the file."),
    (_profiler(_item("volume"), _item("isin")), "10 rows; 2 of 2 requested items are in the file."),
])
def test_the_profiler_summary_agrees_with_the_found_count(data, summary):
    """Phase 7.5, P10 (plan, assumption 22): the verb agrees with the number of items found."""
    assert RULES["profiler"](data)["summary"] == summary


def _columns(n):
    return [{"name": f"C{i}", "distinct_count": 3} for i in range(1, n + 1)]


@pytest.mark.parametrize(("rows", "columns", "summary"), [
    (70, 28, "70 rows, 28 columns."), (1, 1, "1 row, 1 column."), (2, 1, "2 rows, 1 column.")])
def test_a_summary_with_no_requested_items_says_rows_and_columns_never_zero_of_zero(rows, columns, summary):
    """Phase 7.8, task 7.3a: an upload has no requirement, so no 'N of M requested items'."""
    data = {**_profiler(rows=rows, requirements=0), "columns": _columns(columns)}
    out = RULES["profiler"](data)
    assert out["summary"].startswith(summary) and "0 of 0" not in out["summary"]
    assert out["recommended_outcome"] == "meets" and out["gaps"] == []
    # with requested items the coverage sentence stays
    data = {**_profiler(_item("volume"), rows=10), "columns": _columns(columns)}
    assert RULES["profiler"](data)["summary"].startswith("10 rows; 1 of 1 requested items is in the file.")


def _issue(kind, columns, count=12):
    return {"kind": kind, "columns": columns, "count": count, "detail": "x"}


@pytest.mark.parametrize(("issue", "sentence"), [
    (_issue("placeholders", ["SOURCE"]), "Column SOURCE holds placeholders among real values (12 rows)."),
    (_issue("placeholders", ["A", "B"]), "Columns A, B hold placeholders among real values (12 rows)."),
    (_issue("duplicate_key", ["TICKER"], 2), "Column TICKER repeats a key (2 rows)."),
    (_issue("duplicate_key", ["A", "B"], 2), "Columns A, B repeat a key (2 rows)."),
    (_issue("placeholder_only", ["X"]), "Column X holds only empty cells or placeholders (12 rows)."),
    (_issue("date_as_text", ["D"]), "Column D holds dates as text (12 rows)."),
    (_issue("all_empty", ["E"]), "Column E is empty in every row (12 rows)."),
    (_issue("all_empty", ["E", "F"]), "Columns E, F are empty in every row (12 rows)."),
    (_issue("constant", ["K"]), "Column K holds the same value in every row (12 rows)."),
    (_issue("constant", ["K", "L"]), "Columns K, L hold the same value in every row (12 rows)."),
])
def test_an_issue_sentence_is_grammatical_in_the_singular_and_the_plural(issue, sentence):
    out = RULES["profiler"]({**_profiler(_item("volume")), "columns": _columns(2), "issues": [issue]})
    assert sentence in out["summary"] and out["gaps"] == [sentence]
    assert out["recommended_outcome"] == "meets_with_gaps"


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
    ("glossary_builder", _builder(("venue_mic_code", "VARCHAR", [_cand("exchange_mic", 0.67)]), ("x", "DATE", []))),
    ("mapping", _mapping(("date", [_cand("issue_date", 0.5), _cand("maturity_date", 0.5)]))),
    ("transformation", {"columns": [
        {"source_field": "D", "element_key": "e", "element_type": "date", "duckdb_type": "VARCHAR", "null_count": 0,
         "row_count": 3, "placeholder_values": ["NA"], "placeholder_count": 1, "text_date_format": "DD-MM-YYYY",
         "date_format_ambiguous": False, "all_empty": False, "constant": False}]}),
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
