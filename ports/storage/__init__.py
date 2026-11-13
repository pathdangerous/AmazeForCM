"""Storage port. get_blob_store() returns the adapter named by STORAGE_PROVIDER (local | azure_blob)."""

from __future__ import annotations

from ports.config import REPO_ROOT, Settings, get_settings
from ports.storage.base import BlobNotFound, BlobStore, InvalidBlobKey

__all__ = ["BlobNotFound", "BlobStore", "InvalidBlobKey", "get_blob_store"]


def get_blob_store(settings: Settings | None = None) -> BlobStore:
    settings = settings or get_settings()
    if settings.storage_provider == "azure_blob":
        from ports.storage.azure_blob import AzureBlobStore

        return AzureBlobStore()
    from ports.storage.local import LocalBlobStore

    root = settings.local_blob_root
    return LocalBlobStore(root if root.is_absolute() else REPO_ROOT / root)
