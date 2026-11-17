"""Mastering and transformations: PASS-THROUGH ONLY (phase 6 plan, assumption 18; BUILD_PLAN phase 6).

Phase 6 builds the minimal pass-through and says so. Nothing here masters, picks a surviving value, deduplicates or
transforms: a curated row becomes one standard record with the same values under the same element keys. The only
change is how a value is written down, so that it fits JSON and loses nothing: a decimal as its exact string, a
date or datetime as ISO text. The `standard` stage says so in its detail (PASS_THROUGH), so nobody reads this as
mastering. Real mastering and transformations come later (they are not in the PoC path).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

PASS_THROUGH: dict[str, str] = {"mastering": "pass_through", "transformations": "none"}


def json_value(value: Any) -> Any:
    """The value as JSON holds it, exactly."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
