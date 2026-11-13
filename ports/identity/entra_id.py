"""Microsoft Entra ID Identity. STUB: implement after the move to the office laptop.

Plan for the implementation: validate the bearer token from the Authorization header against the
tenant AZURE_TENANT_ID's signing keys (audience AZURE_CLIENT_ID), map app roles or groups to the
seven roles in ports.identity.base.Role, and return a User. list_users reads the app's role
assignments through Microsoft Graph.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ports.config import AZURE_NOT_IMPLEMENTED, DEFAULT_ENV_FILE, read_env
from ports.identity.base import App, User


class EntraIdIdentity:
    name = "entra_id"
    is_stub = True  # /api/health reports this adapter as unavailable
    ENV_KEYS = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID")

    def __init__(self, env_file: Path | None = DEFAULT_ENV_FILE):
        self.config = read_env(self.ENV_KEYS, env_file=env_file)

    def authenticate(self, headers: Mapping[str, str]) -> User | None:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)

    def get_user(self, user_id: str) -> User | None:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)

    def list_users(self, app: App | None = None) -> list[User]:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)
