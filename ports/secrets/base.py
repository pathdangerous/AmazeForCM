"""Secrets port: named secret values (DATABASE_URL, API keys)."""

from __future__ import annotations

from typing import Protocol


class SecretNotFound(LookupError):
    """The secret is not set, or is set to an empty value."""


class Secrets(Protocol):
    name: str

    def get(self, key: str) -> str:
        """Return the secret's value. Raises SecretNotFound."""
        ...
