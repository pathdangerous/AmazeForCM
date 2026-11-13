"""Local BlobStore (STORAGE_PROVIDER=local): one file per blob under a root folder."""

from __future__ import annotations

import os
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

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # readers never see a half-written blob
        return f"local://{key}"

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise BlobNotFound(f"No blob {key!r} in {self.root}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str = "") -> list[str]:
        if not self.root.is_dir():
            return []
        keys = (
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and not p.name.endswith(".tmp")
        )
        return sorted(k for k in keys if k.startswith(prefix))
