"""Notifier port: tell a person something (requester messages, held deliveries)."""

from __future__ import annotations

from typing import Protocol


def check_message(recipient: str, subject: str, body: str) -> None:
    for label, value in (("recipient", recipient), ("subject", subject), ("body", body)):
        if not value.strip():
            raise ValueError(f"{label} is required")


class Notifier(Protocol):
    name: str

    def notify(self, *, recipient: str, subject: str, body: str, link: str | None = None) -> str:
        """Send the notification and return its id. recipient is a user id."""
        ...
