"""The schedule answer the tests give when they create a feed (phase 7.21): the question "Recurring feed or one-time load?"
is required at "Create the feed", so every call to /confirm carries one. No test depends on these particular times."""

from __future__ import annotations

from datetime import time

from core.domain.schedule import ScheduleInput

SCHEDULE = {"kind": "recurring", "frequency": "daily", "expected_time": "06:30:00", "grace_minutes": 120, "timezone": "UTC",
            "notify_owner": True}
ONE_TIME = {"kind": "one_time"}
DEFAULT_INPUT = ScheduleInput(kind="recurring", frequency="daily", expected_time=time(6, 30), grace_minutes=120)
