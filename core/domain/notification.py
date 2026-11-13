"""Notification: a message to a person, stored by the in-app Notifier (ports/notify/inapp.py)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class Notification(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    recipient: str  # user id
    subject: str
    body: str
    link: str | None
    read_at: datetime | None
