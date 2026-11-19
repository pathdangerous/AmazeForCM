"""Mastering is a PASS-THROUGH (phase 6 plan, assumption 18; phase 7.8 decision D6). Transformations are not: they
are the approved ones (phase 7.8 task 4.2, core/engine/transform.py).

Nothing here masters, picks a surviving value or builds a golden record: a curated row becomes one standard record
with the same values under the same element keys. (Transformations and the feed's duplicate-key policy have already
acted in the curated stage.) The only change made here is how a value is written down, so that it fits JSON and
loses nothing: a decimal as its exact string, a date or datetime as ISO text. The `standard` stage says so in its
detail, so nobody reads this as mastering: PASS_THROUGH when no transformation applied, PASS_THROUGH_APPLIED when
at least one did.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

PASS_THROUGH: dict[str, str] = {"mastering": "pass_through", "transformations": "none"}
PASS_THROUGH_APPLIED: dict[str, str] = {"mastering": "pass_through", "transformations": "approved"}


def json_value(value: Any) -> Any:
    """The value as JSON holds it, exactly."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
