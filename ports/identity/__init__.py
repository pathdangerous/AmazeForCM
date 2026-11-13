"""Identity port. get_identity() returns the adapter named by IDENTITY_PROVIDER (dev | entra_id)."""

from __future__ import annotations

from ports.config import Settings, get_settings
from ports.identity.base import ROLES, Identity, User

__all__ = ["ROLES", "Identity", "User", "get_identity"]


def get_identity(settings: Settings | None = None) -> Identity:
    settings = settings or get_settings()
    if settings.identity_provider == "entra_id":
        from ports.identity.entra_id import EntraIdIdentity

        return EntraIdIdentity()
    from ports.identity.dev import DevIdentity

    return DevIdentity()
