"""Phase 7.20: click a rule, see its rows (plan docs/plans/phase-7.20.md). Read only, for any backend role.

GET /api/amaze/rules/{proposal_or_rule_id}/rows?failing_only=&page=&size=
A pending rule proposal runs its SQL in the sandbox on the feed's latest file (the transaction is ended first and no
connection is held while the file is read and the rule runs); an approved rule reads the flags of the newest load.
The answer is a typed result (rows_shown, rule_not_found, no_file, sql_refused), never a bare error.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from apps.api.deps import backend_user, get_app_settings, get_blob_store_port, get_db_session
from apps.api.transactions import read
from core.steps.rule_rows import DraftPlan, LoadPlan, RuleRowsResult, draft_rows, load_rows, rule_rows_plan
from ports.config import Settings
from ports.storage.base import BlobStore

router = APIRouter(prefix="/api/amaze", tags=["rule rows"], dependencies=[Depends(backend_user)])
DbSession = Annotated[Session, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
Blobs = Annotated[BlobStore, Depends(get_blob_store_port)]


@router.get("/rules/{rule_id}/rows", response_model=RuleRowsResult)
def rule_rows(rule_id: UUID, session: DbSession, settings: AppSettings, blobs: Blobs, failing_only: bool = False,
              page: Annotated[int, Query(ge=1)] = 1, size: Annotated[int, Query(ge=1)] = 50) -> RuleRowsResult:
    size = min(size, settings.rule_rows_max_size)
    plan = read(session, lambda: rule_rows_plan(session, rule_id))  # the transaction ends here
    if isinstance(plan, DraftPlan):
        return draft_rows(plan, blob_store=blobs, rule_timeout_seconds=settings.rule_timeout_seconds,
                          failing_only=failing_only, page=page, size=size)
    if isinstance(plan, LoadPlan):
        return read(session, lambda: load_rows(session, plan, failing_only=failing_only, page=page, size=size))
    return plan
