"""Dev Identity (IDENTITY_PROVIDER=dev): seeded synthetic users from fixtures/dev_users.json.

The caller names a user in the X-Dev-User header. No passwords: for local development only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from pydantic import TypeAdapter

from ports.config import REPO_ROOT
from ports.identity.base import App, User

DEFAULT_USERS_FILE = REPO_ROOT / "fixtures" / "dev_users.json"
HEADER = "x-dev-user"


class DevIdentity:
    name = "dev"

    def __init__(self, users_file: Path = DEFAULT_USERS_FILE):
        raw = json.loads(Path(users_file).read_text(encoding="utf-8"))
        users = TypeAdapter(list[User]).validate_python(raw)
        self._users = {user.id: user for user in users}
        if len(self._users) != len(users):
            raise ValueError(f"Duplicate user ids in {users_file}")

    def authenticate(self, headers: Mapping[str, str]) -> User | None:
        value = next((v for k, v in headers.items() if k.lower() == HEADER), None)
        return self.get_user(value.strip()) if value else None

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def list_users(self, app: App | None = None) -> list[User]:
        return [u for u in self._users.values() if app is None or u.app == app]
