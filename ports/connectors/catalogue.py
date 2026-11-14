"""The source catalogue: every source the custom data product knows about (phase 3 plan, assumption 6).

A JSON file (SOURCE_CATALOGUE; the committed one is fixtures/sources/catalogue.json, synthetic only).
Each entry states the facts S2 shows the requester: coverage (the glossary elements it declares and
its other fields) and its conditions of use (licence, permitted use, redistribution, external
approvals). The model never writes these facts; it only ranks sources (phase 3b).

The loader refuses:
- a code that does not start with syn_ (synthetic data only, rule 6);
- a code twice;
- a known source with external approvals (known means Amaze already has the agreement);
- an http source whose host is not allowed (this machine only until the Azure move).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ports.config import REPO_ROOT

DEFAULT_CATALOGUE = REPO_ROOT / "fixtures" / "sources" / "catalogue.json"
DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost")
CODE_PREFIX = "syn_"

Snake = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class UnknownSource(LookupError):
    """No source in the catalogue has this code."""


class SourceConditions(BaseModel):
    """What the requester must accept before the source is used (phase 3b: approval_missing)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    licence: Text
    permitted_use: Text
    redistribution: Text
    external_approvals: list[Text] = Field(default_factory=list)


class SourceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Snake
    name: Text
    provider: Text
    description: Text
    visibility: Literal["public", "private"]
    channel: Snake  # where the source is found, e.g. exchanges, market_data_vendors
    known: bool  # Amaze already has an agreement and a connection
    conditions: SourceConditions
    elements: list[Snake] = Field(default_factory=list)  # glossary element keys the source declares
    other_fields: list[Text] = Field(default_factory=list)  # fields that are not glossary elements
    connector: Literal["local_file", "http"]
    source_ref: Text

    @model_validator(mode="after")
    def _rules(self) -> SourceEntry:
        if not self.code.startswith(CODE_PREFIX):
            raise ValueError(f"source code {self.code!r} must start with {CODE_PREFIX!r} (synthetic data only)")
        if not self.elements and not self.other_fields:
            raise ValueError("a source declares at least one element or other field")
        if self.known and self.conditions.external_approvals:
            raise ValueError(f"known source {self.code!r} cannot need external approvals")
        return self


class SourceCatalogue:
    def __init__(self, entries: list[SourceEntry]):
        self.entries = entries
        self._by_code = {e.code: e for e in entries}

    @classmethod
    def load(cls, path: Path = DEFAULT_CATALOGUE,
             allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS) -> SourceCatalogue:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))["sources"]
        entries = [SourceEntry.model_validate(item) for item in raw]
        codes = [e.code for e in entries]
        twice = sorted({c for c in codes if codes.count(c) > 1})
        if twice:
            raise ValueError(f"source code listed twice: {', '.join(twice)}")
        for entry in entries:
            if entry.connector == "http":
                host = (urlsplit(entry.source_ref).hostname or "").lower()
                if host not in allowed_hosts:
                    raise ValueError(f"source {entry.code!r}: host {host!r} is not allowed "
                                     f"(allowed: {', '.join(allowed_hosts)})")
        return cls(entries)

    def get(self, code: str) -> SourceEntry:
        try:
            return self._by_code[code]
        except KeyError:
            raise UnknownSource(f"No source {code!r} in the catalogue") from None

    def channels(self) -> list[tuple[str, str]]:
        """Each (channel, visibility) once, in catalogue order: where S1 may say to search."""
        seen: dict[tuple[str, str], None] = {}
        for entry in self.entries:
            seen.setdefault((entry.channel, entry.visibility), None)
        return list(seen)

    def known(self) -> list[SourceEntry]:
        """The sources Amaze already has an agreement and a connection for, in catalogue order."""
        return [e for e in self.entries if e.known]
