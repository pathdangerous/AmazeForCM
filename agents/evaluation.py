"""The gold set and its score (phase 5 plan, section 8.1 and assumptions 35 to 38). Pure: no model, no
database. Task 9 adds the MappingEvaluator and python -m agents.evaluate_mappings on top.

A case is right when:
- expected mapped: the agent names exactly one element, and it is the expected one;
- expected tie_to_steward: the agent names two or three elements, and every must-offer element is among them;
- expected no_element_back_to_05: the agent names none (a case with no answer named none).

Reported with the score: the count per category; the mean tie size (over every answer naming two or more);
the candidate recall (each expected or must-offer element that is in the candidate list code built for the
case: a miss outside the list is the filter's, inside it the model's); and the wrong maps (one element
named that the case does not accept: its element, any must-offer element of a tie, none for no element).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

GOLD_FILE = Path(__file__).resolve().parents[1] / "fixtures" / "gold" / "mappings.json"
MAX_TIE = 3


@dataclass(frozen=True)
class GoldCase:
    case: int
    source: str
    column: str
    type: str
    hint: str | None
    category: str
    outcome: str  # mapped | tie_to_steward | no_element_back_to_05
    element: str | None = None
    must_offer: tuple[str, ...] = ()

    @property
    def wanted(self) -> tuple[str, ...]:
        """The elements candidate recall looks for."""
        return (self.element,) if self.element else self.must_offer

    @property
    def accepts(self) -> frozenset[str]:
        return frozenset(self.wanted)


@dataclass(frozen=True)
class GoldSource:
    code: str
    name: str
    description: str


@dataclass(frozen=True)
class Gold:
    version: int
    sources: list[GoldSource]
    cases: list[GoldCase]


def load_gold(path: Path = GOLD_FILE) -> Gold:
    raw = json.loads(path.read_text(encoding="utf-8"))
    sources, cases = [], []
    for s in raw["sources"]:
        sources.append(GoldSource(code=s["code"], name=s["name"], description=s["description"]))
        for c in s["columns"]:
            e = c["expected"]
            cases.append(GoldCase(case=c["case"], source=s["code"], column=c["column"], type=c["type"],
                                  hint=c["hint"], category=c["category"], outcome=e["outcome"],
                                  element=e.get("element"), must_offer=tuple(e.get("must_offer", ()))))
    return Gold(version=raw["version"], sources=sources, cases=cases)


@dataclass(frozen=True)
class Miss:
    case: int
    column: str
    expected: str
    named: tuple[str, ...]


@dataclass(frozen=True)
class WrongMap:
    case: int
    column: str
    named: str


@dataclass(frozen=True)
class Score:
    right: int
    total: int
    per_category: dict[str, tuple[int, int]]
    mean_tie_size: float | None
    recall_found: int
    recall_total: int
    recall_missing: list[tuple[int, str]]
    wrong_maps: list[WrongMap]
    misses: list[Miss]

    @property
    def fraction(self) -> float:
        return self.right / self.total if self.total else 0.0


def is_right(case: GoldCase, named: Sequence[str]) -> bool:
    if case.outcome == "mapped":
        return list(named) == [case.element]
    if case.outcome == "tie_to_steward":
        return 2 <= len(named) <= MAX_TIE and set(case.must_offer) <= set(named)
    return not named


def score(cases: Sequence[GoldCase], answers: Mapping[int, Sequence[str]],
          candidates: Mapping[int, Sequence[str]]) -> Score:
    """answers: per case, the element keys the agent named after code's filtering, best first.
    candidates: per case, the keys of the candidate list code built for it."""
    right, per_category, ties, missing, wrong, misses = 0, {}, [], [], [], []
    found = total = 0
    for c in cases:
        named = tuple(answers.get(c.case, ()))
        ok = is_right(c, named)
        right += ok
        r, n = per_category.get(c.category, (0, 0))
        per_category[c.category] = (r + ok, n + 1)
        if len(named) >= 2:
            ties.append(len(named))
        if len(named) == 1 and named[0] not in c.accepts:
            wrong.append(WrongMap(case=c.case, column=c.column, named=named[0]))
        if not ok:
            misses.append(Miss(case=c.case, column=c.column, expected=c.outcome, named=named))
        offered = set(candidates.get(c.case, ()))
        for element in c.wanted:
            total += 1
            if element in offered:
                found += 1
            else:
                missing.append((c.case, element))
    return Score(right=right, total=len(cases), per_category=per_category,
                 mean_tie_size=sum(ties) / len(ties) if ties else None, recall_found=found, recall_total=total,
                 recall_missing=missing, wrong_maps=wrong, misses=misses)
