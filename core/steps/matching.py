"""Term matching: request text to glossary elements and domains (phase 2 plan, assumptions 7 to 13).

Deterministic code, no model call (decision 1).

- Words: the text is lower-cased; every character other than a-z, 0-9, space, hyphen and underscore
  cuts it into segments; inside a segment hyphens and underscores count as spaces. A phrase never
  spans two segments.
- Phrases: each element's key, name and aliases, and each domain, split the same way. Every word is
  kept, so the alias as_of_date is the phrase "as of date", not "date".
- Left to right inside a segment, the longest phrase starting at the current word wins. Otherwise a
  stop-word is skipped, and any other word joins the current unmatched term. A match, a stop-word or
  the end of a segment closes the term.
- Ticks: an element matched through its key, its name or an alias of two or more words starts
  ticked; one matched only through a one-word alias ("open", "ask") starts unticked.
- A phrase of two elements gives both as candidates (ambiguous), neither ticked. When a domain phrase
  equals an element phrase, the element wins.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict

STOP_WORDS: tuple[str, ...] = (
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at", "by", "from", "with", "per", "as",
    "i", "we", "me", "my", "our", "us", "need", "want", "would", "like", "please", "show", "give", "get",
    "find", "have", "is", "are", "all", "any", "some", "data",
)
_STOP = frozenset(STOP_WORDS)
_SEGMENT_BREAK = re.compile(r"[^a-z0-9 _-]+")
_WORD_BREAK = re.compile(r"[ _-]+")

Words = tuple[str, ...]


def _segments(text: str) -> list[Words]:
    return [ws for seg in _SEGMENT_BREAK.split(text.lower()) if (ws := tuple(w for w in _WORD_BREAK.split(seg) if w))]


def words(text: str) -> Words:
    """The words of a phrase or term, ignoring case and every separator."""
    return tuple(w for segment in _segments(text) for w in segment)


class GlossaryEntry(Protocol):
    key: str
    name: str
    domain: str
    aliases: list[str]


@dataclass
class _Owner:
    elements: dict[str, bool] = field(default_factory=dict)  # element key -> starts ticked
    domain: str | None = None


@dataclass
class Phrases:
    """Every glossary phrase and what it means. Built once per request with build_phrases()."""

    index: dict[Words, _Owner]
    longest: int
    _domain_clashes: list[str]

    def clashes(self) -> dict[str, list[str]]:
        """Each phrase that belongs to two or more elements, with their keys."""
        return {" ".join(p): sorted(o.elements) for p, o in self.index.items() if len(o.elements) > 1}

    def domain_clashes(self) -> list[str]:
        """Each domain phrase that is also an element phrase (the element wins)."""
        return list(self._domain_clashes)

    def is_phrase(self, text: str) -> bool:
        return words(text) in self.index


def build_phrases(elements: Iterable[GlossaryEntry]) -> Phrases:
    index: dict[Words, _Owner] = {}
    domains: set[str] = set()
    for element in elements:
        domains.add(element.domain)
        sources = [(element.key, True), (element.name, True)]
        sources += [(alias, len(words(alias)) > 1) for alias in element.aliases]
        for text, strong in sources:
            phrase = words(text)
            if not phrase:
                continue
            owner = index.setdefault(phrase, _Owner())
            owner.elements[element.key] = owner.elements.get(element.key, False) or strong
    clashes = []
    for domain in sorted(domains):
        owner = index.setdefault(words(domain), _Owner())
        if owner.elements:
            clashes.append(" ".join(words(domain)))
        else:
            owner.domain = domain
    return Phrases(index=index, longest=max((len(p) for p in index), default=0), _domain_clashes=clashes)


class MatchedElement(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    ticked: bool  # whether "Request this data" starts with it ticked


class Ambiguous(BaseModel):
    model_config = ConfigDict(frozen=True)

    phrase: str
    candidates: list[str]  # element keys, alphabetical; none starts ticked


class Match(BaseModel):
    """What the text names. Each list is in order of first appearance, without duplicates."""

    model_config = ConfigDict(frozen=True)

    elements: list[MatchedElement]
    ambiguous: list[Ambiguous]
    domains: list[str]
    terms: list[str]  # unmatched words, grouped; none starts ticked


def match(text: str, phrases: Phrases) -> Match:
    ticked: dict[str, bool] = {}
    ambiguous: dict[str, list[str]] = {}
    domains: dict[str, None] = {}
    terms: dict[str, None] = {}

    def close(term: list[str]) -> None:
        if term:
            terms.setdefault(" ".join(term))
            term.clear()

    for segment in _segments(text):
        term: list[str] = []
        i = 0
        while i < len(segment):
            for n in range(min(phrases.longest, len(segment) - i), 0, -1):
                owner = phrases.index.get(segment[i : i + n])
                if owner is not None:
                    break
            else:
                owner, n = None, 1
            if owner is None:
                if segment[i] in _STOP:
                    close(term)
                else:
                    term.append(segment[i])
            else:
                close(term)
                if len(owner.elements) > 1:
                    ambiguous.setdefault(" ".join(segment[i : i + n]), sorted(owner.elements))
                elif owner.elements:
                    (key, strong), = owner.elements.items()
                    ticked[key] = ticked.get(key, False) or strong
                else:
                    domains.setdefault(owner.domain)
            i += n
        close(term)

    return Match(
        elements=[MatchedElement(key=k, ticked=t) for k, t in ticked.items()],
        ambiguous=[Ambiguous(phrase=p, candidates=c) for p, c in ambiguous.items()],
        domains=list(domains),
        terms=list(terms),
    )
