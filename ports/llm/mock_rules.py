"""MockProvider rules (phase 3 plan, assumption 25).

One pure function per prompt id. It takes the agent's input (the JSON block that ends the user message)
and returns the output as plain data; MockProvider validates it against the response schema. No clock,
no randomness, no file or database access: the same input always gives the same output.

The rules prove the path, not the quality of an answer (phase 3 plan, risk 6).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Any

PRIVATE_WORDS = ("internal", "private", "in-house", "proprietary")
SIMILARITY_THRESHOLD = 0.8


def _words(text: str | None) -> set[str]:
    return set(re.findall(r"[a-z]+(?:-[a-z]+)*", (text or "").lower()))


def _phrase(text: str) -> str:
    """Lower case, '_' and runs of spaces read as one space."""
    return " ".join(text.lower().replace("_", " ").split())


def intent(data: dict[str, Any]) -> dict[str, Any]:
    """private if the text, purpose or note holds a private word; otherwise public. Search the
    channels of that visibility, in the order given."""
    found = set().union(*(_words(data.get(key)) for key in ("requirement_text", "purpose", "note")))
    private = sorted(found & set(PRIVATE_WORDS))
    visibility = "private" if private else "public"
    channels = [c["channel"] for c in data.get("channels", []) if c["visibility"] == visibility]
    if private:
        reason = f"The request mentions '{private[0]}', so the data must come from the firm's own systems."
        provider = "The firm's own systems"
    else:
        reason = "Nothing in the request says the data must stay in the firm, so outside providers are searched."
        provider = "External data providers"
    return {"visibility": visibility, "provider": provider, "where_to_search": channels, "reason": reason}


def source_discovery(data: dict[str, Any]) -> dict[str, Any]:
    """Rank by the most requested items covered, then the fewest other declared fields, then the code.
    A source that covers nothing is left out."""
    elements = set(data.get("requested_elements", []))
    terms = {_phrase(t) for t in data.get("requested_terms", [])}
    wanted = len(elements) + len(terms)
    scored = []
    for candidate in data.get("candidates", []):
        declared = set(candidate.get("elements", []))
        others = {_phrase(f) for f in candidate.get("other_fields", [])}
        covered = len(elements & declared) + len(terms & others)
        if covered:
            extra = len(declared) + len(others) - covered
            scored.append((-covered, extra, candidate["code"], covered))
    ranked = [{"source_code": code,
               "reason": f"Covers {covered} of {wanted} requested items."}
              for _, _, code, covered in sorted(scored)]
    return {"ranked": ranked}


def _singular(phrase: str) -> str:
    words = phrase.split()
    if words and len(words[-1]) > 3:
        last = words[-1]
        if last.endswith("ies"):
            last = last[:-3] + "y"
        elif last.endswith("s") and not last.endswith("ss"):
            last = last[:-1]
        words[-1] = last
    return " ".join(words)


def discovery_assistant(data: dict[str, Any]) -> dict[str, Any]:
    """For each unmatched term: drop a plural ending and look for a glossary name or alias; if none,
    the closest name or alias at or above the similarity threshold. Ties go to the element key."""
    phrases = []
    for element in data.get("glossary", []):
        for phrase in [element["name"], *element.get("aliases", [])]:
            phrases.append((_phrase(phrase), element["key"]))
    suggestions = []
    for term in data.get("unmatched_terms", []):
        wanted = _singular(_phrase(term))
        exact = sorted(key for phrase, key in phrases if phrase in (wanted, _phrase(term)))
        if exact:
            suggestions.append({"term": term, "element_key": exact[0],
                                "reason": f"'{term}' is another way to say this element."})
            continue
        best = sorted((-SequenceMatcher(None, wanted, phrase).ratio(), key) for phrase, key in phrases)
        if best and -best[0][0] >= SIMILARITY_THRESHOLD:
            suggestions.append({"term": term, "element_key": best[0][1],
                                "reason": f"'{term}' is spelled almost like this element."})
    return {"suggestions": suggestions}


def profiler(data: dict[str, Any]) -> dict[str, Any]:
    """Phase 4 plan, assumption 15. A requested item not in the file, or with empty cells, is a gap (each
    gap once, in order). No gap: meets. Gaps: meets_with_gaps. No requested item in the file at all:
    rejected. A spec-only profile: meets_with_gaps if the source declares a requested item, else rejected."""
    items = [item for coverage in data.get("coverage", []) for item in coverage["items"]]
    rows = data.get("row_count") or 0
    if data.get("outcome") == "spec_only_low_confidence":
        if any(i["declared"] for i in items):
            gap = f"No data rows were profiled: {data.get('detail')}"
            return {"summary": "Only the source's spec could be read.", "recommended_outcome": "meets_with_gaps",
                    "gaps": [gap], "reasons": []}
        return {"summary": "Only the source's spec could be read, and it declares no requested item.",
                "recommended_outcome": "rejected", "gaps": [],
                "reasons": ["The source declares none of the requested items"]}
    found = [i for i in items if i["in_file"]]
    if items and not found:
        return {"summary": f"{rows} rows; none of the requested items is in the file.",
                "recommended_outcome": "rejected", "gaps": [], "reasons": ["No requested item is in the file"]}
    gaps: list[str] = []
    for i in items:
        if not i["in_file"]:
            gap = f"{i['item']} is not in the file"
        elif i["null_rate"]:
            gap = f"{i['item']} is empty in {round(i['null_rate'] * rows)} of {rows} rows"
        else:
            continue
        if gap not in gaps:
            gaps.append(gap)
    summary = f"{rows} rows; {len({i['item'] for i in found})} of {len({i['item'] for i in items})} requested items are in the file."
    return {"summary": summary, "recommended_outcome": "meets_with_gaps" if gaps else "meets", "gaps": gaps,
            "reasons": []}


_FAMILIES = (
    (re.compile(r"^(VARCHAR|TEXT|STRING)"), "string"),
    (re.compile(r"^U?(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT)$|^INT[1248]?$"), "integer"),
    (re.compile(r"^(DOUBLE|FLOAT|REAL|DECIMAL|NUMERIC)"), "decimal"),
    (re.compile(r"^DATE$"), "date"),
    (re.compile(r"^(TIMESTAMP|DATETIME)"), "datetime"),
    (re.compile(r"^BOOL"), "boolean"),
)


def _family(duckdb_type: str | None) -> str:
    """The glossary data type of a DuckDB type; text when unknown."""
    t = (duckdb_type or "").strip().upper()
    return next((family for pattern, family in _FAMILIES if pattern.match(t)), "string")


def glossary_builder(data: dict[str, Any]) -> dict[str, Any]:
    """Phase 5 plan, assumption 16. Per column: one candidate scoring 0.5 or more whose type fits is the
    column under another name (same_as); two or more: nothing (step 06 chooses); none: a new element keyed
    and named from the column's words, typed from its DuckDB family, in the source's main domain."""
    answers = []
    for column in data.get("columns", []):
        name = column["name"]
        fitting = [c for c in column.get("candidates", []) if c["score"] >= 0.5 and c["type_fits"]]
        answer = {"column": name, "same_as": None, "new_element": None}
        if len(fitting) == 1:
            answer.update(same_as=fitting[0]["key"], reason=f"'{name}' reads like {fitting[0]['name']}.")
        elif fitting:
            answer["reason"] = f"'{name}' could be any of {len(fitting)} elements; the data steward chooses."
        else:
            words = re.findall(r"[a-z0-9]+", name.lower())
            key = "_".join(words)
            key = key if key[:1].isalpha() else f"col_{key}"
            label = " ".join(words).capitalize()
            answer["new_element"] = {
                "key": key, "name": label, "domain": data.get("source_domain"), "data_type": _family(column.get("type")),
                "definition": f"{label}: column {name} of {data.get('source_name')}. To be confirmed by the glossary owner.",
                "aliases": []}
            answer["reason"] = f"No glossary element reads like '{name}'."
        answers.append(answer)
    return {"answers": answers}


def mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Phase 5 plan, assumption 33, over the candidates whose type fits: the one scoring 1.0; else the
    spec's hinted one; else every one scoring 0.5 or more (none, one, or a tie), best first."""
    answers = []
    for column in data.get("columns", []):
        fitting = [c for c in column.get("candidates", []) if c["type_fits"]]
        exact = [c for c in fitting if c["score"] == 1.0]
        hinted = [c for c in fitting if c["from_hint"]]
        if len(exact) == 1:
            chosen, reason = exact, "The column's name is this element's name or alias."
        elif hinted:
            chosen, reason = hinted[:1], "The source's spec names this element and its type fits."
        else:
            chosen = [c for c in fitting if c["score"] >= 0.5]
            reason = ("No candidate reads like the column with a fitting type." if not chosen else
                      "The column reads like this element." if len(chosen) == 1 else
                      "The column reads like each of these; the data steward chooses.")
        answers.append({"column": column["name"], "elements": [c["key"] for c in chosen], "reason": reason})
    return {"answers": answers}


RULES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "intent": intent,
    "source_discovery": source_discovery,
    "discovery_assistant": discovery_assistant,
    "profiler": profiler,
    "glossary_builder": glossary_builder,
    "mapping": mapping,
}
