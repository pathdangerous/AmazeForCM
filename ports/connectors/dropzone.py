"""Drop-folder connector (phase 7.8, S4, decision D5): the recurring files of an uploaded feed.

An uploaded feed reads `dropzone/<feed code>`: the folder <root>/<feed code>/. fetch() returns the newest regular
file there (modification time; a tie goes to the name that sorts last). Hidden files, *.tmp and *.part (a file still
being written) are ignored. No file, or no folder, is SourceUnavailable("no file in the drop folder").

write_to_dropzone() is the one writer (the first upload and a re-upload): it writes <UTC timestamp>_<safe name>.part
and renames it, so a reader never sees half a file.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ports.connectors.base import FetchedFile, SourceUnavailable, guess_content_type

PREFIX = "dropzone/"
NO_FILE = "no file in the drop folder"
_FEED_CODE = re.compile(r"^[a-z][a-z0-9_]*$")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_IGNORED_SUFFIXES = (".tmp", ".part")


def _folder(root: Path, feed_code: str) -> Path:
    if not _FEED_CODE.match(feed_code):
        raise SourceUnavailable(f"{feed_code!r} is not a feed code")
    folder = (root / feed_code).resolve()
    if not folder.is_relative_to(root.resolve()):
        raise SourceUnavailable(f"The drop folder of {feed_code!r} is outside {root}")
    return folder


class DropzoneConnector:
    name = "dropzone"

    def __init__(self, root: Path):
        self._root = Path(root)

    def __enter__(self) -> DropzoneConnector:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Nothing to close; present so every connector is used the same way."""

    def fetch(self, source_ref: str) -> FetchedFile:
        if not source_ref.startswith(PREFIX):
            raise SourceUnavailable(f"Source {source_ref!r} is not a drop folder")
        folder = _folder(self._root, source_ref[len(PREFIX):])
        try:
            candidates = [p for p in folder.iterdir()
                          if p.is_file() and not p.name.startswith(".") and not p.name.lower().endswith(_IGNORED_SUFFIXES)]
        except OSError:  # no folder (or not a folder)
            candidates = []
        if not candidates:
            raise SourceUnavailable(NO_FILE)
        newest = max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name))
        try:
            content = newest.read_bytes()
        except OSError as exc:
            raise SourceUnavailable(f"The file {newest.name!r} in the drop folder cannot be read: {exc}") from exc
        return FetchedFile(source_ref=source_ref, filename=newest.name, content=content,
                           content_type=guess_content_type(newest.name))


def remove_dropzone(root: Path, feed_code: str) -> int:
    """Delete the feed's whole drop folder (phase 7.9: a deleted feed leaves no file the same code could pick up
    again). Returns how many files went; no folder is no error."""
    folder = _folder(Path(root), feed_code)
    if not folder.is_dir():
        return 0
    files = sum(1 for p in folder.rglob("*") if p.is_file())
    shutil.rmtree(folder)
    return files


def write_to_dropzone(root: Path, feed_code: str, filename: str, data: bytes) -> str:
    """Store data in the feed's drop folder as <UTC timestamp>_<safe name>; returns that name. Atomic."""
    folder = _folder(Path(root), feed_code)
    folder.mkdir(parents=True, exist_ok=True)
    base = re.split(r"[\\/]", filename)[-1]
    safe = _UNSAFE.sub("_", base).lstrip(".") or "file"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    name, counter = f"{stamp}_{safe}", 1
    while (folder / name).exists():  # two writes in one microsecond
        name, counter = f"{stamp}-{counter}_{safe}", counter + 1
    part = folder / f"{name}.part"
    part.write_bytes(data)
    os.replace(part, folder / name)
    return name
