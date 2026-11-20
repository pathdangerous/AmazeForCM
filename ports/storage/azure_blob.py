"""Azure Blob / Data Lake Storage Gen2 BlobStore. STUB: implement after the move to the office laptop.

Plan for the implementation: azure-storage-blob BlobServiceClient with DefaultAzureCredential,
account https://<AZURE_STORAGE_ACCOUNT>.blob.core.windows.net, container AZURE_STORAGE_CONTAINER.
Validate keys with ports.storage.base.check_key so both adapters accept the same keys.
"""

from __future__ import annotations

from pathlib import Path

from ports.config import AZURE_NOT_IMPLEMENTED, DEFAULT_ENV_FILE, read_env


class AzureBlobStore:
    name = "azure_blob"
    is_stub = True  # /api/health reports this adapter as unavailable
    ENV_KEYS = ("AZURE_STORAGE_ACCOUNT", "AZURE_STORAGE_CONTAINER")

    def __init__(self, env_file: Path | None = DEFAULT_ENV_FILE):
        self.config = read_env(self.ENV_KEYS, env_file=env_file)

    def put(self, key: str, data: bytes) -> str:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)

    def get(self, key: str) -> bytes:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)

    def exists(self, key: str) -> bool:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)

    def delete(self, key: str) -> None:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)

    def list_keys(self, prefix: str = "") -> list[str]:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)
