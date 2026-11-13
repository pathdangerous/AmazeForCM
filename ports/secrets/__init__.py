"""Secrets port. get_secrets() returns the adapter named by SECRETS_PROVIDER (env | key_vault)."""

from __future__ import annotations

from ports.config import Settings, get_settings
from ports.secrets.base import SecretNotFound, Secrets

__all__ = ["SecretNotFound", "Secrets", "get_secrets"]


def get_secrets(settings: Settings | None = None) -> Secrets:
    settings = settings or get_settings()
    if settings.secrets_provider == "key_vault":
        from ports.secrets.key_vault import KeyVaultSecrets

        return KeyVaultSecrets()
    from ports.secrets.env import EnvSecrets

    return EnvSecrets()
