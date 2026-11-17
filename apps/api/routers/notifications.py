"""GET /api/notifications: the signed-in user's own notifications, newest 50 (phase 7 plan, assumption 37).

Any signed-in dev user, backend or requester; read only (no read state). Another user's are never listed. Both
UIs show this as a Notifications tab, which is how "a person is notified" is seen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.deps import current_user, get_db_session
from apps.api.transactions import read
from core.repositories.notifications import NotificationRepository
from ports.identity import User

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

INBOX = 50


class NotificationOnPage(BaseModel):
    id: UUID
    created_at: datetime
    subject: str
    body: str
    link: str | None
    read_at: datetime | None


@router.get("", response_model=list[NotificationOnPage])
def my_notifications(session: Annotated[Session, Depends(get_db_session)],
                     user: Annotated[User, Depends(current_user)]) -> list[NotificationOnPage]:
    rows = read(session, lambda: NotificationRepository(session).list_for(user.id, limit=INBOX))
    return [NotificationOnPage(**r.model_dump(exclude={"recipient"})) for r in rows]
