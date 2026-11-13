"""Identity port: who is calling, and in which roles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol, get_args

from pydantic import BaseModel, Field

Role = Literal[
    "requester",
    "backend_team",
    "data_owner",
    "data_steward",
    "checker",
    "glossary_owner",
    "operations",
]
ROLES: tuple[str, ...] = get_args(Role)
CONSUMER_ROLES = frozenset({"requester"})  # end users see consumer-ui; every other role uses backend-ui

App = Literal["backend", "consumer"]


class User(BaseModel):
    id: str
    display_name: str
    email: str
    roles: list[Role] = Field(min_length=1)

    @property
    def app(self) -> App:
        return "consumer" if set(self.roles) <= CONSUMER_ROLES else "backend"


class Identity(Protocol):
    name: str

    def authenticate(self, headers: Mapping[str, str]) -> User | None:
        """The calling user, or None if the request carries no valid identity."""
        ...

    def get_user(self, user_id: str) -> User | None: ...

    def list_users(self, app: App | None = None) -> list[User]: ...
