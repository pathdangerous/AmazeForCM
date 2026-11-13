"""In-app Notifier (NOTIFY_PROVIDER=inapp): one row per notification in the notifications table.

Given a session, it adds and flushes (the caller owns the transaction). Without one, it opens its
own session and commits.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from ports.db import Base, get_sessionmaker
from ports.notify.base import check_message


class NotificationRow(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_recipient", "recipient"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    # clock_timestamp() (migration 0004): notifications are listed newest first, also within one transaction.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())
    recipient: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InAppNotifier:
    name = "inapp"

    def __init__(self, session: Session | None = None, *, session_factory: sessionmaker | None = None):
        self._session = session
        self._session_factory = session_factory

    def notify(self, *, recipient: str, subject: str, body: str, link: str | None = None) -> str:
        check_message(recipient, subject, body)
        row = NotificationRow(recipient=recipient, subject=subject, body=body, link=link)
        if self._session is not None:
            self._session.add(row)
            self._session.flush()
            return str(row.id)
        with (self._session_factory or get_sessionmaker())() as session:
            session.add(row)
            session.commit()
            return str(row.id)
