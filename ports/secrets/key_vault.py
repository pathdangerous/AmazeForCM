"""Azure Key Vault Secrets adapter. STUB: implement after the move to the office laptop.

Plan for the implementation: azure-keyvault-secrets SecretClient with DefaultAzureCredential,
vault URL from AZURE_KEY_VAULT_URL. Key Vault names allow only letters, digits and '-', so map
DATABASE_URL to DATABASE-URL. Raise SecretNotFound for a missing secret.
"""

from __future__ import annotations

from pathlib import Path

from ports.config import AZURE_NOT_IMPLEMENTED, DEFAULT_ENV_FILE, read_env


class KeyVaultSecrets:
    name = "key_vault"
    is_stub = True  # /api/health reports this adapter as unavailable
    ENV_KEYS = ("AZURE_KEY_VAULT_URL",)

    def __init__(self, env_file: Path | None = DEFAULT_ENV_FILE):
        self.config = read_env(self.ENV_KEYS, env_file=env_file)

    def get(self, key: str) -> str:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)
