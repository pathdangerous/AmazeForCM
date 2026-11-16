"""fixtures/gold/mappings.json (phase 5 plan, section 8.1 and assumption 35): 31 cases, frozen at version 1.

Any change to the file fails test_the_gold_file_is_frozen until its version and the pinned hash change
together, and the change is recorded in the phase 5 plan, section 17. Never change it to fit a score.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from agents.evaluation import GOLD_FILE, load_gold
from core.steps.candidates import type_family

ROOT = Path(__file__).resolve().parents[1]
SEED_KEYS = {e["key"] for e in json.loads((ROOT / "fixtures/glossary/enterprise_glossary.json")
                                          .read_text(encoding="utf-8"))["elements"]}
FROZEN = (1, "190233703c66c37b5010e28fc06c0fda5f243c3e613e21275d03ea6e948407c8")


def test_the_gold_file_is_frozen():
    raw = json.loads(GOLD_FILE.read_text(encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert (raw["version"], digest) == FROZEN


def test_thirty_one_cases_in_four_synthetic_sources():
    gold = load_gold()
    assert [c.case for c in gold.cases] == list(range(1, 32))
    assert [s.code for s in gold.sources] == ["syn_gold_eod_prices", "syn_gold_vendor_quotes",
                                             "syn_gold_volume_stats", "syn_gold_bond_terms"]
    assert all(s.description.strip() for s in gold.sources)


def test_the_categories_are_those_of_section_8_1():
    assert Counter(c.category for c in load_gold().cases) == {
        "easy": 6, "alias only": 9, "hint": 2, "abbreviation": 2, "tie": 3, "type mismatch": 3,
        "no element": 2, "context": 2, "misleading name": 2}


def test_the_expected_outcomes():
    cases = load_gold().cases
    assert Counter(c.outcome for c in cases) == {"mapped": 22, "tie_to_steward": 3, "no_element_back_to_05": 6}
    for c in cases:
        if c.outcome == "mapped":
            assert c.element and not c.must_offer
        elif c.outcome == "tie_to_steward":
            assert c.element is None and len(c.must_offer) >= 2
        else:
            assert c.element is None and not c.must_offer


def test_every_element_named_is_a_seed_element_and_every_type_has_a_family():
    for c in load_gold().cases:
        named = {c.element, c.hint, *c.must_offer} - {None}
        assert named <= SEED_KEYS, c.case
        assert type_family(c.type) is not None, c.case


def test_the_hard_cases_are_in():
    by_case = {c.case: c for c in load_gold().cases}
    assert (by_case[15].column, by_case[15].element) == ("quote_ccy", "price_currency")
    assert (by_case[19].column, by_case[19].outcome) == ("high", "no_element_back_to_05")
    assert (by_case[31].hint, by_case[31].outcome) == ("lot_size", "no_element_back_to_05")


def test_the_file_holds_no_values():
    text = GOLD_FILE.read_text(encoding="utf-8").lower()
    assert '"values"' not in text and '"sample' not in text and '"rows"' not in text
