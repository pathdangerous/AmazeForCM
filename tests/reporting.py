"""Loads on chosen dates with chosen flags, for the phase 7.15 tests (patterns, reports, audit).

Built the way the database allows (as tests/configured.certified_load does): a scheduled load naming a go-live, its
arrival, its standard records, four completed stages, loaded, a certification, then released or held by its score.
`flags` says how many rows each rule flagged (the first rows); `provider_date` goes into the records' price_date, the
date element of tests/configured.loadable_feed(). Each helper flushes; the test's session rolls back.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from core.domain.load import STAGES, Load, LoadStageDraft, StandardRecordDraft
from core.repositories.certifications import LoadCertificationRepository
from core.repositories.loads import LoadRepository
from core.repositories.standard_records import StandardRecordRepository


def loaded_on(session, feed_id: UUID, go_live_id: UUID, received: datetime, *, flags: dict[str, int] | None = None,
              provider_date: date | None = None, rows: int = 10, file_ref: str | None = None,
              rerun_of: UUID | None = None, threshold: int = 90) -> Load:
    """A certified scheduled load received at `received` (UTC). Each rule in `flags` flags that many rows from row 1."""
    flags = flags or {}
    repo = LoadRepository(session)
    extra = {"rerun_of_load_id": rerun_of, "rerun_reason": "Again."} if rerun_of else {}
    load = repo.create(feed_id=feed_id, file_ref=file_ref or f"raw/syn/{received:%Y%m%d%H%M%S}/{len(flags)}.csv",
                       purpose="scheduled", go_live_id=go_live_id, received_at=received.astimezone(UTC), **extra)
    repo.record_arrival(load.id, outcome="on_time_complete", detail={},
                        content_sha256=hashlib.sha256(load.id.bytes).hexdigest())
    drafts, flagged = [], 0
    for number in range(1, rows + 1):
        keys = sorted(rule for rule, count in flags.items() if number <= count)
        flagged += bool(keys)
        values = {"price_date": provider_date.isoformat()} if provider_date else {}
        drafts.append(StandardRecordDraft(row_number=number, values=values, flags=keys))
    StandardRecordRepository(session).add_many(load_id=load.id, feed_id=feed_id, records=drafts)
    now = datetime.now(UTC)
    repo.record_stages(load.id, [LoadStageDraft(stage=name, outcome="stage_completed", rows_in=rows, rows_out=rows,
                                                detail={}, started_at=now, finished_at=now) for name in STAGES])
    repo.finish(load.id, status="loaded")
    score = Decimal(100 * (rows - flagged) / rows).quantize(Decimal("0.1"))
    released = score >= threshold
    LoadCertificationRepository(session).add(
        load_id=load.id, feed_id=feed_id, rows=rows, flagged_rows=flagged, trust_score=score,
        threshold=Decimal(threshold), outcome="released" if released else "held_consumers_warned",
        rule_counts={rule: count for rule, count in flags.items()})
    return repo.set_status(load.id, "released" if released else "held")
