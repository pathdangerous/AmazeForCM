"""BlobStore port: files by key. Keys use '/' separators, e.g. raw/feed-a/2026-01-01.csv."""

from __future__ import annotations

from typing import Protocol


class BlobNotFound(LookupError):
    """No blob has this key."""


class InvalidBlobKey(ValueError):
    """The key is empty, absolute, contains '\\' or ':', has an empty segment, ends with '/',
    or has a segment starting with '.' (which covers '.' and '..')."""


def check_key(key: str) -> list[str]:
    """Validate a key and return its segments. Shared by every adapter so all accept the same keys.

    Segments starting with '.' are reserved: the local adapter uses dot-names for unfinished writes.
    """
    parts = key.split("/")
    if (
        not key
        or "\\" in key
        or ":" in key
        or any(part == "" or part.startswith(".") for part in parts)
    ):
        raise InvalidBlobKey(f"Invalid blob key {key!r}")
    return parts


class BlobStore(Protocol):
    name: str

    def put(self, key: str, data: bytes) -> str:
        """Store data under key, replacing any existing blob. Returns a reference to the blob."""
        ...

    def get(self, key: str) -> bytes:
        """Raises BlobNotFound."""
        ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None:
        """Remove the blob. A key that holds no blob is not an error (a delete may be repeated)."""
        ...

    def list_keys(self, prefix: str = "") -> list[str]:
        """Keys starting with prefix, sorted."""
        ...
