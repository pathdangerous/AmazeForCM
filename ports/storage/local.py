"""Local BlobStore (STORAGE_PROVIDER=local): one file per blob under a root folder.

A write goes to a hidden temp file next to the blob (".<name>.<random>.tmp"), then replaces the blob
in one step, so readers never see half a blob. Keys cannot start a segment with '.', so a temp file
can never collide with a blob, and list_keys skips every dot-name.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from ports.storage.base import BlobNotFound, InvalidBlobKey, check_key


class LocalBlobStore:
    name = "local"

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = self.root.joinpath(*check_key(key)).resolve()
        if not path.is_relative_to(self.root):
            raise InvalidBlobKey(f"Invalid blob key {key!r}")
        return path

    @staticmethod
    def _temp_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._temp_path(path)
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return f"local://{key}"

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise BlobNotFound(f"No blob {key!r} in {self.root}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list_keys(self, prefix: str = "") -> list[str]:
        if not self.root.is_dir():
            return []
        keys = []
        for path in self.root.rglob("*"):
            relative = path.relative_to(self.root)
            if path.is_file() and not any(part.startswith(".") for part in relative.parts):
                keys.append(relative.as_posix())
        return sorted(k for k in keys if k.startswith(prefix))
