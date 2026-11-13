"""Email or Teams Notifier (NOTIFY_PROVIDER=email). STUB: implement after the move to the office laptop.

Plan for the implementation: resolve the recipient's address through the Identity port, send email
from NOTIFY_EMAIL_FROM (for example Azure Communication Services or Microsoft Graph sendMail), and
post to TEAMS_WEBHOOK_URL when it is set. Keep writing the in-app row as well, so the UI history
stays complete.
"""

from __future__ import annotations

from pathlib import Path

from ports.config import AZURE_NOT_IMPLEMENTED, DEFAULT_ENV_FILE, read_env


class EmailNotifier:
    name = "email"
    is_stub = True  # /api/health reports this adapter as unavailable
    ENV_KEYS = ("NOTIFY_EMAIL_FROM", "TEAMS_WEBHOOK_URL")

    def __init__(self, env_file: Path | None = DEFAULT_ENV_FILE):
        self.config = read_env(self.ENV_KEYS, env_file=env_file)

    def notify(self, *, recipient: str, subject: str, body: str, link: str | None = None) -> str:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)
