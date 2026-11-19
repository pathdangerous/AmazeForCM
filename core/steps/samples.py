"""Sample values for a model (phase 7.8, decision D3).

samples_for_model() is the one place that decides whether a column's sample values reach a model. Every agent
input that carries column facts uses it: the ProfilerAgent today; the glossary builder, mapping and
transformation agents call the same function in their stages. The columns in an input carry no samples
themselves; the values travel only in the separate `samples` field, and only when SEND_SAMPLE_VALUES is true.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.domain.profiling import FileColumn
from ports.config import Settings

SAMPLE_LIMIT = 5


def samples_for_model(columns: Sequence[FileColumn], settings: Settings) -> dict[str, list[str]]:
    """{} when SEND_SAMPLE_VALUES is false; otherwise, per column that has any, at most 5 distinct non-blank
    values from FileColumn.samples (in their order)."""
    if not settings.send_sample_values:
        return {}
    found: dict[str, list[str]] = {}
    for column in columns:
        values = list(dict.fromkeys(v for v in column.samples if v.strip()))[:SAMPLE_LIMIT]
        if values:
            found[column.name] = values
    return found
