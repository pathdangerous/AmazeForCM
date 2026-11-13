"""Local file connector: reads a file under a base folder (default fixtures/sources)."""

from __future__ import annotations

from pathlib import Path

from ports.config import REPO_ROOT
from ports.connectors.base import FetchedFile, SourceUnavailable, guess_content_type

DEFAULT_SOURCES_ROOT = REPO_ROOT / "fixtures" / "sources"


class LocalFileConnector:
    name = "local_file"

    def __init__(self, base_dir: Path = DEFAULT_SOURCES_ROOT):
        self._base = Path(base_dir).resolve()

    def fetch(self, source_ref: str) -> FetchedFile:
        path = (self._base / source_ref).resolve()
        if not path.is_relative_to(self._base):
            raise SourceUnavailable(f"Source {source_ref!r} is outside {self._base}")
        if not path.is_file():
            raise SourceUnavailable(f"Source file {source_ref!r} not found in {self._base}")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise SourceUnavailable(f"Source file {source_ref!r} cannot be read: {exc}") from exc
        return FetchedFile(
            source_ref=source_ref, filename=path.name, content=content, content_type=guess_content_type(path.name)
        )
