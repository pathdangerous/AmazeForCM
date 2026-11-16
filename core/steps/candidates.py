"""Candidate elements, new columns, coverage and the glossary builder's outcome (phase 5 plan, assumptions
5, 6, 8, 9 and 14).

Pure and deterministic: no model, no database. No agent is sent the whole glossary; each gets, per column
or term, the list candidate_list() builds.

- Score of a name against an element: 1.0 when its words equal the words of the element's key, name or
  an alias; otherwise the best, over those phrases, of the word overlap (shared words / all words) and the
  character similarity (difflib ratio of the two phrases), the second counted only at 0.8 or more.
- Type fit: the DuckDB type's family equals the element's data_type, or an integer column goes into a
  decimal element. An unknown type (None) fits every element.
- A list: elements scoring 0.3 or more, best first then by key; the first 5 and any that tie with the
  fifth, never more than 10. The spec's hinted element is added (and marked) if absent; a list already at
  10 then drops its last other candidate.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from core.domain.glossary import GlossaryElementDraft
from core.domain.mapping import Candidate, ElementOption, NewColumn
from core.domain.profiling import FileColumn, SourceSpec
from core.steps.matching import Words, words

MIN_SCORE = 0.3
SIMILAR = 0.8
FIRST = 5
MAX_CANDIDATES = 10


class Element(Protocol):
    key: str
    name: str
    data_type: str
    definition: str
    aliases: list[str]


# ---------------------------------------------------------------- score and type fit


def phrases(element: Element) -> list[Words]:
    return [words(p) for p in (element.key, element.name, *element.aliases)]


def score(name: str, element: Element) -> float:
    w = words(name)
    best = 0.0
    for p in phrases(element):
        if p == w:
            return 1.0
        union = set(w) | set(p)
        overlap = len(set(w) & set(p)) / len(union) if union else 0.0
        ratio = difflib.SequenceMatcher(None, " ".join(w), " ".join(p)).ratio()
        best = max(best, overlap, ratio if ratio >= SIMILAR else 0.0)
    return best


_INTEGER = re.compile(r"^(U?(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT)|INT[1248]?|LONG|SHORT)$")
_DECIMAL = re.compile(r"^(DOUBLE|FLOAT[48]?|REAL|DECIMAL(\(\d+,\s*\d+\))?|NUMERIC(\(\d+,\s*\d+\))?)$")


def type_family(duckdb_type: str) -> str | None:
    t = duckdb_type.strip().upper()
    if t in ("VARCHAR", "TEXT", "STRING") or t.startswith("VARCHAR("):
        return "string"
    if _INTEGER.match(t):
        return "integer"
    if _DECIMAL.match(t):
        return "decimal"
    if t == "DATE":
        return "date"
    if t.startswith("TIMESTAMP") or t == "DATETIME":
        return "datetime"
    if t in ("BOOLEAN", "BOOL"):
        return "boolean"
    return None


def type_fits(column_type: str | None, data_type: str) -> bool:
    if column_type is None:
        return True
    family = type_family(column_type)
    return family is not None and (family == data_type or (family, data_type) == ("integer", "decimal"))


# ---------------------------------------------------------------- candidate lists


def _candidate(element: Element, s: float, column_type: str | None, from_hint: bool) -> Candidate:
    return Candidate(key=element.key, name=element.name, data_type=element.data_type,
                     definition=element.definition, aliases=list(element.aliases), score=s,
                     from_hint=from_hint, type_fits=type_fits(column_type, element.data_type))


def candidate_list(name: str, column_type: str | None, elements: Iterable[Element],
                   hint: str | None = None, *, also: Iterable[str] = ()) -> list[Candidate]:
    """One list for one column or term; with also, each element scores its best over name and those forms."""
    pool = list(elements)
    forms = list(dict.fromkeys((name, *also)))

    def best(e: Element) -> float:
        return max(score(f, e) for f in forms)

    scored = sorted(((best(e), e) for e in pool), key=lambda t: (-t[0], t[1].key))
    kept = [(s, e) for s, e in scored if s >= MIN_SCORE]
    if len(kept) > FIRST:
        fifth = kept[FIRST - 1][0]
        kept = [(s, e) for s, e in kept if s >= fifth]
    kept = kept[:MAX_CANDIDATES]
    hinted = next((e for e in pool if e.key == hint), None) if hint else None
    if hinted is not None and all(e.key != hint for _, e in kept):
        if len(kept) == MAX_CANDIDATES:
            kept = kept[:-1]
        kept.append((best(hinted), hinted))
        kept.sort(key=lambda t: (-t[0], t[1].key))
    return [_candidate(e, s, column_type, from_hint=e.key == hint) for s, e in kept]


def singular(term: str) -> str:
    """The term with a plural ending dropped from its last word (the DiscoveryAssistant's mock rule)."""
    ws = list(words(term))
    if ws and len(ws[-1]) > 3:
        last = ws[-1]
        if last.endswith("ies"):
            last = last[:-3] + "y"
        elif last.endswith("s") and not last.endswith("ss"):
            last = last[:-1]
        ws[-1] = last
    return " ".join(ws)


def term_candidates(terms: Iterable[str], elements: Iterable[Element]) -> list[Candidate]:
    """Step 17 (assumption 7): one list per term, scored on the term and its singular form (so at most 10
    per term); each element once, first seen first. No type is known, so none is dropped for its type."""
    pool = list(elements)
    seen: dict[str, Candidate] = {}
    for term in terms:
        for x in candidate_list(term, None, pool, also=(singular(term),)):
            seen.setdefault(x.key, x)
    return list(seen.values())


# ---------------------------------------------------------------- new columns and coverage (assumptions 8, 9)


def new_columns(columns: Sequence[FileColumn], spec: SourceSpec, sent_back: Iterable[str] = ()) -> list[NewColumn]:
    """The profile's columns step 05 looks at, in file order: undeclared, declared as an other field, or
    sent back by step 06. A column the spec declares as a glossary element is never new unless sent back."""
    back = set(sent_back)
    found = []
    for column in columns:
        declared = spec.columns.get(column.name)
        is_back = column.name in back
        if declared in spec.elements and not is_back:
            continue
        found.append(NewColumn(name=column.name, type=column.type, null_rate=column.null_rate,
                               distinct_count=column.distinct_count,
                               declared_as=declared if declared in spec.other_fields else None, sent_back=is_back))
    return found


def covered_by(column: NewColumn, elements: Iterable[Element], *, mapped: Iterable[str] = (),
               asked: Iterable[str] = ()) -> Literal["name", "mapping", "asked"] | None:
    """Why a new column is skipped, or None. A column sent back skips the name rule only."""
    if column.name in set(mapped):
        return "mapping"
    if column.name in set(asked):
        return "asked"
    if column.sent_back:
        return None
    names = {words(column.name)} | ({words(column.declared_as)} if column.declared_as else set())
    owners = {e.key for e in elements for p in phrases(e) if p in names}
    return "name" if len(owners) == 1 else None


# ---------------------------------------------------------------- classify (assumption 14)

GlossaryOutcome = Literal["proposed", "duplicate_alias", "conflict_to_owner"]


@dataclass(frozen=True)
class Classified:
    outcome: GlossaryOutcome
    options: list[ElementOption]
    recommended: int | None  # index into options; None for a conflict


def alias_of(column_name: str) -> str:
    return "_".join(words(column_name))


def classify_element(column: NewColumn, candidates: Sequence[Candidate], *, same_as: str | None,
                     new_element: GlossaryElementDraft | None) -> Classified | None:
    """Code names the outcome, not the agent. None: nothing to propose (the column is left to step 06)."""
    keys = {x.key for x in candidates}
    if same_as is not None and same_as not in keys:
        same_as = None  # the agent never names an element that was not offered
    if same_as is None and new_element is None:
        return None
    names = {words(column.name)}
    if new_element is not None:
        names |= {words(new_element.key), words(new_element.name)}
    hits = [x for x in candidates if x.key == same_as or any(p in names for p in phrases(x))]
    alias = alias_of(column.name)
    if not hits:
        assert new_element is not None
        return Classified("proposed", [ElementOption(kind="new_element", key=new_element.key, element=new_element)], 0)
    if len(hits) == 1 and hits[0].type_fits:
        return Classified("duplicate_alias", [ElementOption(kind="alias", key=hits[0].key, alias=alias)], 0)
    options = [ElementOption(kind="alias", key=x.key, alias=alias)
               for x in hits if x.type_fits and words(alias) not in phrases(x)]
    if new_element is not None:
        taken = {p for x in candidates for p in phrases(x)}
        if words(new_element.key) not in taken and words(new_element.name) not in taken:
            options.append(ElementOption(kind="new_element", key=new_element.key, element=new_element))
    return Classified("conflict_to_owner", options, None)
