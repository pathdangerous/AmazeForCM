"""Notifier port. get_notifier() returns the adapter named by NOTIFY_PROVIDER (inapp | email)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ports.config import Settings, get_settings
from ports.notify.base import Notifier

__all__ = ["Notifier", "get_notifier"]


def get_notifier(settings: Settings | None = None, *, session: Session | None = None) -> Notifier:
    settings = settings or get_settings()
    if settings.notify_provider == "email":
        from ports.notify.email_teams import EmailNotifier

        return EmailNotifier()
    from ports.notify.inapp import InAppNotifier

    return InAppNotifier(session)
