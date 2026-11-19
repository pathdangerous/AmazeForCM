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


_KEY_WORDS = ("TICKER", "ISIN", "CUSIP", "SEDOL", "ID", "CODE")
# (one column, several columns): "Column A holds ..." and "Columns A, B hold ..."
_ISSUE_WORDS = {"duplicate_key": ("repeats a key", "repeat a key"),
                "placeholder_only": ("holds only empty cells or placeholders", "hold only empty cells or placeholders"),
                "placeholders": ("holds placeholders among real values", "hold placeholders among real values"),
                "date_as_text": ("holds dates as text", "hold dates as text"),
                "all_empty": ("is empty in every row", "are empty in every row"),
                "constant": ("holds the same value in every row", "hold the same value in every row")}


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _issue_sentence(issue: dict[str, Any]) -> str:
    columns = issue["columns"]
    several = len(columns) > 1
    singular, plural = _ISSUE_WORDS.get(issue["kind"], (issue["kind"], issue["kind"]))
    return (f"{'Columns' if several else 'Column'} {', '.join(columns)} {plural if several else singular} "
            f"({_plural(issue['count'], 'row')}).")


def _key_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Phase 7.8, task 3.2: the first column named like an identifier (not constant or empty); else the column with
    the most distinct values when it equals the row count; else none."""
    columns = data.get("columns", [])
    usable = [c for c in columns if not c.get("constant") and not c.get("all_empty")]
    named = next((c for c in usable if any(w in c["name"].upper() for w in _KEY_WORDS)), None)
    if named:
        return {"key_columns": [named["name"]], "confidence": 0.9,
                "key_reason": f"{named['name']} is named like an identifier."}
    rows = data.get("row_count") or 0
    best = max(usable, key=lambda c: c["distinct_count"], default=None)  # max keeps the first of equals
    if best and rows and best["distinct_count"] == rows:
        return {"key_columns": [best["name"]], "confidence": 0.6,
                "key_reason": f"{best['name']} has a different value in every row."}
    return {"key_columns": [], "confidence": 0.3, "key_reason": "No column looks like a row identifier."}


def profiler(data: dict[str, Any]) -> dict[str, Any]:
    """Phase 4 plan, assumption 15, and phase 7.8, task 3.2. The key is proposed first; any issue Amaze found
    is worded in the summary and makes a 'meets' a 'meets_with_gaps'. The coverage rule: see _profiler_outcome."""
    out = _profiler_outcome(data)
    key = _key_fields(data)
    issues = data.get("issues", [])
    if issues:
        sentences = [_issue_sentence(i) for i in issues]
        out["summary"] = f"{out['summary']} Issues found: {' '.join(sentences)}"
        if out["recommended_outcome"] == "meets":
            out["recommended_outcome"] = "meets_with_gaps"
            out["gaps"] = sentences
    if key["key_columns"]:
        out["summary"] += f" Proposed key: {', '.join(key['key_columns'])}."
    return {**out, **key}


def _profiler_outcome(data: dict[str, Any]) -> dict[str, Any]:
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
    if not items:  # an upload has no requirement: say what the file holds, never "0 of 0 requested items"
        return {"summary": f"{_plural(rows, 'row')}, {_plural(len(data.get('columns', [])), 'column')}.",
                "recommended_outcome": "meets", "gaps": [], "reasons": []}
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
    summary = f"{rows} rows; {(n := len({i['item'] for i in found}))} of {len({i['item'] for i in items})} requested items {'is' if n == 1 else 'are'} in the file."
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


# Phase 7.8, task 6.1 (plan assumption 41): the security master's columns, named in plain words. The mock cannot know
# a unit, so none is given here; step 05 fills the few the seed names (fixtures/glossary/units.json).
_PLAIN_COLUMNS: dict[str, tuple[str, str, str, str, list[str]]] = {  # column words -> key, name, type, definition, aliases
    "source": ("data_source", "Data source", "string", "The data source each row comes from, as named in the file.",
               ["source"]),
    "shortname": ("short_name", "Short name", "string", "A short name of the instrument or company, as given by the "
                  "source.", ["shortname"]),
    "sector": ("sector", "Sector", "string", "The industry sector of the company, as given by the source.", []),
    "region": ("region", "Region", "string", "The geographic region of the company, as given by the source.", []),
    "f_period": ("fiscal_period", "Fiscal period", "string", "The fiscal period the estimates refer to, as given by "
                 "the source.", ["f_period"]),
    "record_date": ("record_date", "Record date", "date", "The date of the record, as given by the source.", []),
}
_ABBREVIATIONS = (  # two-word entries first: the longest match from the left wins
    ("px_bps", "price to book"), ("net_gaap", "GAAP net income"), ("esp_gaap", "GAAP earnings per share"),
    ("div_yld", "dividend yield"), ("net_income", "net income"), ("net_debt", "net debt"),
    ("gross_margin", "gross margin"), ("eps", "earnings per share"), ("bps", "book value per share"),
    ("dps", "dividend per share"), ("capex", "capital expenditure"), ("ebitda", "EBITDA"), ("ebit", "EBIT"),
    ("ev", "enterprise value"), ("pe", "price to earnings"), ("roe", "return on equity"), ("opp", "operating profit"),
    ("gaap", "GAAP"))
_CHANGE = re.compile(r"^(?P<base>.+)_(?P<n>\d+)_(?P<unit>week|month)_percent_change$")


def _estimate_phrase(tokens: list[str]) -> str:
    phrase, i = [], 0
    while i < len(tokens):
        for abbreviation, words_ in _ABBREVIATIONS:
            size = abbreviation.count("_") + 1
            if "_".join(tokens[i:i + size]) == abbreviation:
                phrase.append(words_)
                i += size
                break
        else:
            phrase.append(tokens[i])
            i += 1
    return " ".join(phrase)


def _security_master_element(name: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """A new element for a security master column the rule knows by name (None for any other column)."""
    snake = "_".join(re.findall(r"[a-z0-9]+", name.lower()))
    if snake in _PLAIN_COLUMNS:
        key, label, data_type, definition, aliases = _PLAIN_COLUMNS[snake]
        domain = "security_master" if "security_master" in data.get("domains", []) else data.get("source_domain")
        return {"key": key, "name": label, "domain": domain, "data_type": data_type, "definition": definition,
                "aliases": aliases, "unit": None}
    if not snake.startswith("best_") or len(snake) <= 5:
        return None
    rest, change = snake[5:].replace("best_", ""), None
    found = _CHANGE.match(rest)
    if found:
        rest, change = found["base"], (int(found["n"]), found["unit"])
    phrase = _estimate_phrase(rest.split("_"))
    where = f"as given by {data.get('source_name')} (column {name})."
    if change is None:
        label = f"Best {phrase} estimate"
        definition = f"Consensus estimate of {phrase}, {where}"
    else:
        span = f"{change[0]} {change[1]}{'' if change[0] == 1 else 's'}"
        label = f"Best {phrase} estimate, change over {span}"
        definition = f"Change in the consensus estimate of {phrase} over {span}, {where}"
    # an estimate is a number even when the file holds text placeholders in the column
    return {"key": snake, "name": label, "domain": "estimates", "data_type": "decimal", "definition": definition,
            "aliases": [], "unit": None}


def glossary_builder(data: dict[str, Any]) -> dict[str, Any]:
    """Phase 5 plan, assumption 16. Per column: one candidate scoring 0.5 or more whose type fits is the
    column under another name (same_as); two or more: nothing (step 06 chooses); none: a new element keyed
    and named from the column's words, typed from its DuckDB family, in the source's main domain.
    Phase 7.8 (task 6.1): a column of the security master that no element already is by name (no candidate
    scores 1.0) gets its plain-word element first (_security_master_element)."""
    answers = []
    for column in data.get("columns", []):
        name = column["name"]
        fitting = [c for c in column.get("candidates", []) if c["score"] >= 0.5 and c["type_fits"]]
        answer = {"column": name, "same_as": None, "new_element": None}
        known = (None if any(c["score"] == 1.0 for c in column.get("candidates", []))
                 else _security_master_element(name, data))
        if known is not None:
            answer.update(new_element=known, reason=f"No element has the meaning of '{name}'; "
                                                    f"proposed as {known['name']}.")
        elif len(fitting) == 1:
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


def rule_recommender(data: dict[str, Any]) -> dict[str, Any]:
    """Phase 6 plan, assumption 13: one rule per template offered (each element's, then the feed's), the SQL as
    the template wrote it. The verdict, the reason and the back-test are left for code to fill."""
    offers = [o for element in data.get("elements", []) for o in element.get("templates", [])]
    offers += data.get("feed_templates", [])
    return {"rules": [{"rule_key": o["rule_key"], "description": o["description"], "sql": o["sql"],
                       "element_keys": o["element_keys"], "template": o["template"]} for o in offers]}


def review_summariser(data: dict[str, Any]) -> dict[str, Any]:
    """Phase 6 plan, assumption 23: a summary built from the facts in the input, naming every unmapped column,
    every rejected glossary proposal and every rule. Pure: the same facts give the same words."""
    mapped = [f"{m['source_field']} as {m['element_key']}" for m in data.get("mapped", [])]
    unmapped = data.get("unmapped_columns", [])
    rejected = [r["source_field"] for r in data.get("rejected_glossary", [])]
    rules = data.get("rules", [])
    load = data.get("test_load", {})
    parts = [f"Feed {data.get('feed_code')} ({data.get('source_name')}) loaded {load.get('rows', 0)} test rows through "
             f"{len(load.get('stages', []))} stages.",
             f"It maps {len(mapped)} column(s): {', '.join(mapped) or 'none'}."]
    parts.append(f"{len(unmapped)} column(s) are not mapped and will not be loaded: {', '.join(unmapped)}."
                 if unmapped else "Every column of the file is mapped.")
    if rejected:
        parts.append(f"The glossary owner rejected proposals for: {', '.join(rejected)}.")
    parts.append(f"{len(rules)} rule(s) apply: {', '.join(r['rule_key'] for r in rules) or 'none'}.")
    risks = [f"Column {c} is not loaded." for c in unmapped] + [f"Glossary proposal for {c} was rejected." for c in rejected]
    flags = load.get("flags", {})
    risks += [f"Rule {key} flagged {count} row(s) in the test load." for key, count in sorted(flags.items()) if count]
    pending = data.get("pending", {})
    if any(pending.values()):
        risks.append("Proposals still wait: " + ", ".join(f"{n} {k}" for k, n in pending.items() if n) + ".")
    return {"summary": " ".join(parts), "highlights": [f"{len(mapped)} column(s) mapped, {len(rules)} rule(s)."],
            "risks": risks}


def transformation(data: dict[str, Any]) -> dict[str, Any]:
    """Phase 7.8, task 4.3: per mapped column, from the facts code found. Placeholders found: placeholder_to_null with
    those spellings. A text date column mapped to a date element: parse_date with the detected format (confidence 0.6
    when the format is ambiguous, so a person checks it), after the placeholder step. Nothing else: no steps."""
    answers = []
    for column in data.get("columns", []):
        steps: list[dict[str, Any]] = []
        parts: list[str] = []
        confidence = 0.95
        if column.get("placeholder_count") and column.get("placeholder_values"):
            steps.append({"op": "placeholder_to_null", "values": list(column["placeholder_values"])})
            parts.append(f"{column['placeholder_count']} cell(s) hold a placeholder "
                         f"({', '.join(column['placeholder_values'])}), so they become empty")
        if column.get("text_date_format") and column.get("element_type") == "date":
            steps.append({"op": "parse_date", "format": column["text_date_format"]})
            ambiguous = bool(column.get("date_format_ambiguous"))
            parts.append(f"the text dates are read as {column['text_date_format']}"
                         + (" (ambiguous: a person must check the format)" if ambiguous else ""))
            if ambiguous:
                confidence = 0.6
        reason = f"{column['source_field']}: " + "; ".join(parts) + "." if parts else "Nothing to clean in this column."
        answers.append({"source_field": column["source_field"], "steps": steps, "reason": reason,
                        "confidence": confidence})
    return {"columns": answers}


RULES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "intent": intent,
    "source_discovery": source_discovery,
    "discovery_assistant": discovery_assistant,
    "profiler": profiler,
    "glossary_builder": glossary_builder,
    "mapping": mapping,
    "rule_recommender": rule_recommender,
    "review_summariser": review_summariser,
    "transformation": transformation,
}
