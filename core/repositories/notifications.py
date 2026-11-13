"""NotificationRepository: reads notifications. Writing stays with the Notifier port (ports/notify)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.notification import Notification
from ports.notify.inapp import NotificationRow as Row


class NotificationRepository:
    def __init__(self, session: Session):
        self._session = session

    def list_for(self, recipient: str) -> list[Notification]:
        query = select(Row).where(Row.recipient == recipient).order_by(Row.created_at.desc(), Row.id)
        return [Notification.model_validate(r) for r in self._session.scalars(query)]
