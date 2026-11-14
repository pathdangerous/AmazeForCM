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


RULES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "intent": intent,
    "source_discovery": source_discovery,
    "discovery_assistant": discovery_assistant,
}
