"""Dev sign-in support: list the seeded users (IDENTITY_PROVIDER=dev only) and show the signed-in user."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from apps.api.deps import current_user, get_app_settings, get_identity_port
from ports.config import Settings
from ports.identity import User
from ports.identity.base import App

router = APIRouter()


class UserOut(BaseModel):
    id: str
    display_name: str
    email: str
    roles: list[str]
    app: App

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(id=user.id, display_name=user.display_name, email=user.email, roles=user.roles, app=user.app)


@router.get("/api/dev/users", response_model=list[UserOut])
def dev_users(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    app: App | None = None,
) -> list[UserOut]:
    if settings.identity_provider != "dev":
        raise HTTPException(status_code=404, detail="Not found")
    return [UserOut.of(u) for u in get_identity_port(request).list_users(app=app)]


@router.get("/api/me", response_model=UserOut)
def me(user: Annotated[User, Depends(current_user)]) -> UserOut:
    return UserOut.of(user)
