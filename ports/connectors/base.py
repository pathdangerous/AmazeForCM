"""Source connectors for the custom data product (S4 fetch). Chosen per source, not by an env selector."""

from __future__ import annotations

import mimetypes
from typing import Protocol

from pydantic import BaseModel

# Built-in table only: mimetypes.guess_type() would also read the Windows registry, which differs per machine.
_MIME = mimetypes.MimeTypes()


def guess_content_type(filename: str) -> str | None:
    return _MIME.guess_type(filename)[0]


class SourceUnavailable(Exception):
    """The source cannot be read now (missing, unreachable, error status). Maps to the source_down outcome."""


class FetchedFile(BaseModel):
    source_ref: str
    filename: str
    content: bytes
    content_type: str | None

    @property
    def size(self) -> int:
        return len(self.content)


class SourceConnector(Protocol):
    name: str

    def fetch(self, source_ref: str) -> FetchedFile:
        """Raises SourceUnavailable."""
        ...
