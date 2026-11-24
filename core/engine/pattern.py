"""The value pattern of an exception group (phase 7.14, plan section 2.1): the Python twin of the database function
amaze_value_pattern() (migration 0033). Deterministic, no model (rule 2). tests/test_exceptions_view.py checks that
both give the same answer.

For each element key (distinct, in code point order, as the database's "C" collation), the value as jsonb ->> gives
it (a string as it is, anything else as its JSON text), with digit runs as 9, upper-case ASCII runs as A and
lower-case ASCII runs as a; a missing value, a null or spaces only is <empty>; joined with ' | '. No key: (row).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

EMPTY, ROW, JOIN = "<empty>", "(row)", " | "
_DIGITS, _UPPER, _LOWER = re.compile(r"[0-9]+"), re.compile(r"[A-Z]+"), re.compile(r"[a-z]+")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value)


def _shape(value: Any) -> str:
    raw = _text(value)
    if raw is None or raw.strip(" ") == "":  # btrim() strips spaces only
        return EMPTY
    return _LOWER.sub("a", _UPPER.sub("A", _DIGITS.sub("9", raw)))


def value_pattern(values: Mapping[str, Any], element_keys: Iterable[str]) -> str:
    keys = sorted(set(element_keys))
    if not keys:
        return ROW
    return JOIN.join(_shape(values.get(key)) for key in keys)
