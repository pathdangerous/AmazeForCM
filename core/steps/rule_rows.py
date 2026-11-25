"""Click a rule, see its rows (phase 7.20, plan docs/plans/phase-7.20.md). Read only: no model call, no write.

Two sources, one answer:
  a) a rule proposal that is not approved: its SQL runs in the sandbox on the feed's latest fetched file, through the
     draft configuration, the way the back-test reads a delivery (draft_rows(): no session, so no connection is held
     while the file is read and the rule runs);
  b) an approved rule: the flags of the feed's newest load that has records (load_rows(): no SQL runs).
Outcomes (rule 3): rows_shown, rule_not_found, no_file, sql_refused.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.engine.curate import curated_rows_sql, curated_sql, match_header, uncast_sql
from core.engine.passthrough import json_value
from core.engine.sandbox import FileUnreadable, RuleFailed, RuleRefused, Sandbox
from core.repositories.configuration import RuleRepository
from core.repositories.errors import ProposalNotFound
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.repositories.standard_records import StandardRecordRepository
from core.steps.drafts import draft_config
from core.steps.result import StepResult
from ports.storage.base import BlobNotFound, BlobStore


class RuleInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_key: str
    description: str
    sql: str
    element_keys: list[str]


class ColumnInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    affected: bool


class RowInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    row: int
    values: dict[str, Any]
    passes: bool  # false for a failing row and for a waived one
    waived: bool


class Counts(BaseModel):
    model_config = ConfigDict(frozen=True)

    rows: int
    failing: int
    waived: int
    shown: int  # the rows the filter keeps (all rows, or the failing ones)


class RowsShown(StepResult):
    outcome: Literal["rows_shown"] = "rows_shown"
    rule: RuleInfo
    source: Literal["draft", "load"]
    load_id: UUID | None
    columns: list[ColumnInfo]
    rows: list[RowInfo]
    counts: Counts
    page: int
    size: int


class RuleNotFound(StepResult):
    outcome: Literal["rule_not_found"] = "rule_not_found"


class NoFile(StepResult):
    outcome: Literal["no_file"] = "no_file"
    reason: str


class SqlRefused(StepResult):
    outcome: Literal["sql_refused"] = "sql_refused"
    reason: str


RuleRowsResult = Annotated[RowsShown | RuleNotFound | NoFile | SqlRefused, Field(discriminator="outcome")]


@dataclass(frozen=True)
class DraftPlan:
    """Read from the database, then the session ends: what the sandbox needs, and nothing that holds a connection."""

    rule: RuleInfo
    file_ref: str | None
    columns: tuple  # SimpleNamespace(source_field, element_key, data_type), in mapping order
    steps_of: dict  # source field in lower case -> the approved (or draft) transformation steps


@dataclass(frozen=True)
class LoadPlan:
    feed_id: UUID
    rule: RuleInfo


def rule_rows_plan(session: Session, rule_id: UUID) -> DraftPlan | LoadPlan | RuleNotFound:
    """Read only. A `rules` row id or an approved rule proposal id is source (b); any other rule proposal is source (a)."""
    stored = RuleRepository(session).get(rule_id)
    proposals = ProposalRepository(session)
    try:
        proposal = proposals.get(stored.proposal_id if stored else rule_id)
    except ProposalNotFound:
        return RuleNotFound()
    if proposal.kind != "rule" or proposal.feed_id is None:
        return RuleNotFound()
    payload = proposal.payload
    rule = RuleInfo(rule_key=payload["rule_key"], description=stored.description if stored else payload["description"],
                    sql=stored.sql if stored else payload["sql"], element_keys=list(payload.get("element_keys", [])))
    if stored is not None or proposal.status == "approved":
        return LoadPlan(feed_id=proposal.feed_id, rule=rule)
    draft = draft_config(session, proposal.feed_id)
    fetch = SourceFetchRepository(session).latest_fetched(proposal.feed_id)
    columns = tuple(SimpleNamespace(source_field=m.source_field, element_key=m.element_key,
                                    data_type=draft.types[m.element_key]) for m in draft.mappings)
    return DraftPlan(rule=rule, file_ref=fetch.blob_ref if fetch else None, columns=columns,
                     steps_of={t.source_field.lower(): tuple(t.steps) for t in draft.transformations})


def _shown(rule: RuleInfo, source: Literal["draft", "load"], load_id: UUID | None, keys: list[str], rows: list[RowInfo],
           counts: Counts, page: int, size: int) -> RowsShown:
    affected = [k for k in dict.fromkeys(rule.element_keys) if k in keys]  # the rule's columns first, in its own order
    columns = [ColumnInfo(key=k, affected=True) for k in affected]
    columns += [ColumnInfo(key=k, affected=False) for k in keys if k not in affected]
    return RowsShown(rule=rule, source=source, load_id=load_id, columns=columns, rows=rows, counts=counts, page=page,
                     size=size)


def draft_rows(plan: DraftPlan, *, blob_store: BlobStore, rule_timeout_seconds: float, failing_only: bool, page: int,
               size: int) -> RowsShown | NoFile | SqlRefused:
    """Source (a). No session: the file is read through BlobStore.get() and the rule runs in the locked sandbox."""
    if plan.file_ref is None:
        return NoFile(reason="The feed has no fetched file yet: there is no file to run the rule on.")
    if not plan.columns:
        return NoFile(reason="The feed has no mapped column to read its file through.")
    try:
        content = blob_store.get(plan.file_ref)
    except BlobNotFound:
        return NoFile(reason="The feed's latest file is no longer stored.")
    box = Sandbox(timeout_seconds=rule_timeout_seconds)
    try:
        with tempfile.TemporaryDirectory(prefix="amaze-rule-rows-") as folder:
            path = Path(folder) / "file.csv"
            path.write_bytes(content)
            try:
                box.load_csv(path)
            except FileUnreadable as exc:
                return NoFile(reason=f"DuckDB could not read the file: {exc}")
        header = [r[0] for r in box.execute("DESCRIBE raw") if r[0] != "_row"]
        found, missing, _ = match_header(plan.columns, header, plan.steps_of)
        if missing:
            return NoFile(reason=f"{len(missing)} mapped column(s) are not in the file: {', '.join(missing)}")
        if box.execute("SELECT count(*) FROM raw")[0][0] == 0:
            return NoFile(reason="The file has no data rows.")
        box.execute(curated_sql(found))
        uncast = sum(len(box.execute(uncast_sql(f))) for f in found)
        if uncast:
            return NoFile(reason=f"{uncast} value(s) do not cast to their element's type")
        keys = [f.element_key for f in found]
        # shortcut: the whole curated table of one file is held for this request (rows are read before lock());
        # upgrade to a paged read if a file ever outgrows memory.
        curated = box.execute(curated_rows_sql(found))
        box.lock()
        try:
            flagged = set(box.run_rule(plan.rule.sql))
        except (RuleRefused, RuleFailed) as exc:
            return SqlRefused(reason=str(exc))
    finally:
        box.close()
    every = [RowInfo(row=int(r[0]), values={k: json_value(v) for k, v in zip(keys, r[1:], strict=True)},
                     passes=int(r[0]) not in flagged, waived=False) for r in curated]
    kept = [r for r in every if not r.passes] if failing_only else every
    start = (page - 1) * size
    counts = Counts(rows=len(every), failing=len(flagged), waived=0, shown=len(kept))
    return _shown(plan.rule, "draft", None, keys, kept[start:start + size], counts, page, size)


def load_rows(session: Session, plan: LoadPlan, *, failing_only: bool, page: int, size: int) -> RowsShown | NoFile:
    """Source (b). Read only; the flags and waivers the engine wrote, and no rule runs."""
    load = LoadRepository(session).latest_with_records(plan.feed_id)
    if load is None:
        return NoFile(reason="The feed has no loaded file yet: no row has been checked by this rule.")
    records = StandardRecordRepository(session)
    key = plan.rule.rule_key
    total, failing, waived = records.rule_counts(load.id, key)
    page_of = records.rule_rows(load.id, key, failing_only=failing_only, offset=(page - 1) * size, limit=size)
    rows = [RowInfo(row=r.row_number, values=r.values, passes=key not in r.flags and key not in r.waived,
                     waived=key in r.waived)
            for r in page_of]
    keys = list(page_of[0].values) if page_of else []
    counts = Counts(rows=total, failing=failing, waived=waived, shown=failing if failing_only else total)
    return _shown(plan.rule, "load", load.id, keys, rows, counts, page, size)
