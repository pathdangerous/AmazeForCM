"""Step 13, the run engine: raw -> curated -> validated -> standard model (phase 6 plan, assumptions 14 to 20).

Rule 2: deterministic code only. No model call, no import of agents or ports.llm. The engine takes its whole
configuration from MappingRepository.current_for_feed() and RuleRepository.current_for_feed() (phase 5 note 1);
an unmapped column is not loaded.

Three parts, as for the fetch and the profile, so no database connection is held while the file is read or a
rule runs:
    plan_load(session, load_id)       reads (ArrivalNotChecked for a scheduled load step 12 has not passed): the
                                      load, the feed, the current mappings and rules, the element
                                      types, and whether a scheduled load's configuration still is the one the
                                      latest go-live approved. The caller then ends the transaction.
    execute_load(plan, blob_store=..) no session: BlobStore.get() and the DuckDB sandbox. Never raises for an
                                      expected case: a failed stage is a FailedRolledBack result.
    record_load(session, plan, ..)    locks the feed row, then the load row, checks the load is still received,
                                      and writes the stage rows, the standard records and the status in one
                                      savepoint; if that write fails, the savepoint is rolled back and the
                                      completed stages, a failed `standard` stage and failed_rolled_back are
                                      written instead. A failed file leaves no standard record.

Each stage's result is a typed StageCompleted or FailedRolledBack. A value that does not cast to its element's
type holds the whole file (assumption 16); a rule flags rows and never fails the load, but a rule whose SQL errors
or times out does (assumption 17). Mastering and transformations are the pass-through marked in passthrough.py.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.load import STAGES, Load, LoadStageDraft, Stage, StandardRecordDraft
from core.engine.curate import Found, curated_rows_sql, curated_sql, match_header, uncast_sql
from core.engine.passthrough import PASS_THROUGH, json_value
from core.engine.sandbox import FileUnreadable, RuleFailed, RuleRefused, Sandbox
from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.errors import ArrivalNotChecked, FeedNotRegistered, LoadNotReceived
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.go_lives import GoLiveRepository
from core.repositories.loads import LoadRepository
from core.repositories.standard_records import StandardRecordRepository
from core.steps.result import StepResult
from ports.storage.base import BlobNotFound, BlobStore

FIRST_UNCAST = 5  # the values listed when a file is held for values that do not cast
PASSING_ARRIVALS = ("on_time_complete", "additive_parked")  # step 12's outcomes after which a load goes on


class StageCompleted(StepResult):
    outcome: Literal["stage_completed"] = "stage_completed"
    stage: Stage
    rows_in: int
    rows_out: int
    detail: dict[str, Any]


class FailedRolledBack(StepResult):
    outcome: Literal["failed_rolled_back"] = "failed_rolled_back"
    stage: Stage
    reason: str  # a code (values_do_not_cast, rule_failed, ...); the sentence is detail["message"]
    detail: dict[str, Any]
    rows_in: int = 0


RunResult = Annotated[StageCompleted | FailedRolledBack, Field(discriminator="outcome")]


class MappedColumn(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_field: str
    element_key: str
    data_type: str
    mapping_id: UUID


class RuleToRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: UUID
    rule_key: str
    sql: str


class LoadPlan(BaseModel):
    """What one load needs, read in a transaction the caller then ends."""

    model_config = ConfigDict(frozen=True)

    load: Load
    columns: list[MappedColumn]
    rules: list[RuleToRun]
    configuration_changed: bool  # a scheduled load whose configuration is not the latest go-live's


class LoadOutcome(BaseModel):
    """What execute_load found: the stage results (the last may be a failure), the records of a loaded file,
    and when each stage began and ended."""

    model_config = ConfigDict(frozen=True)

    stages: list[RunResult]
    records: list[StandardRecordDraft]
    timings: dict[str, tuple[datetime, datetime]]

    @property
    def failed(self) -> FailedRolledBack | None:
        last = self.stages[-1]
        return last if isinstance(last, FailedRolledBack) else None


class LoadRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    load: Load
    stages: list[RunResult]


def _failed(stage: Stage, reason: str, message: str, *, rows_in: int = 0, **detail: Any) -> FailedRolledBack:
    return FailedRolledBack(stage=stage, reason=reason, rows_in=rows_in, detail={"message": message, **detail})


# ---------------------------------------------------------------- plan


def plan_load(session: Session, load_id: UUID) -> LoadPlan:
    """Read only. LoadNotFound, LoadNotReceived, FeedNotRegistered."""
    load = LoadRepository(session).get(load_id)
    if load.status != "received":
        raise LoadNotReceived(f"Load {load_id} is {load.status}, not received")
    if load.purpose == "scheduled" and load.arrival_outcome not in PASSING_ARRIVALS:  # step 12 comes first
        raise ArrivalNotChecked(f"Scheduled load {load_id} has not passed step 12 (arrival outcome: "
                                f"{load.arrival_outcome or 'none'}); it is not run")
    feed = FeedRepository(session).get(load.feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}; only a registered feed is loaded")
    glossary = GlossaryRepository(session)
    mappings = MappingRepository(session).current_for_feed(feed.id)
    rules = RuleRepository(session).current_for_feed(feed.id)
    changed = False
    if load.purpose == "scheduled":  # assumption 15: loaded only as the latest go-live approved it
        live = GoLiveRepository(session).latest(feed.id)
        changed = (live is None or set(live.mapping_ids) != {m.id for m in mappings}
                   or set(live.rule_ids) != {r.id for r in rules})
    return LoadPlan(
        load=load, configuration_changed=changed,
        columns=[MappedColumn(source_field=m.source_field, element_key=m.element_key, mapping_id=m.id,
                              data_type=glossary.get_current(m.element_key).data_type) for m in mappings],
        rules=[RuleToRun(rule_id=r.id, rule_key=r.rule_key, sql=r.sql) for r in rules])


# ---------------------------------------------------------------- execute (no session)


def execute_load(plan: LoadPlan, *, blob_store: BlobStore, rule_timeout_seconds: float) -> LoadOutcome:
    """No database. The stages in order, stopping at the first that fails."""
    stages: list[RunResult] = []
    timings: dict[str, tuple[datetime, datetime]] = {}
    records: list[StandardRecordDraft] = []
    box = Sandbox(timeout_seconds=rule_timeout_seconds)
    state: dict[str, Any] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="amaze-load-") as folder:
            steps = (("raw", lambda: _raw(plan, blob_store, box, Path(folder) / "file.csv", state)),
                     ("curated", lambda: _curated(plan, box, state)),
                     ("validated", lambda: _validated(plan, box, state)),
                     ("standard", lambda: _standard(state, records)))
            for name, step in steps:
                started = datetime.now(UTC)
                result = step()
                timings[name] = (started, datetime.now(UTC))
                stages.append(result)
                if isinstance(result, FailedRolledBack):
                    records.clear()
                    break
    finally:
        box.close()
    return LoadOutcome(stages=stages, records=records, timings=timings)


def _raw(plan: LoadPlan, blobs: BlobStore, box: Sandbox, path: Path, state: dict) -> RunResult:
    load = plan.load
    if plan.configuration_changed:
        return _failed("raw", "configuration_changed_since_go_live",
                       "The mappings or rules are not the ones the latest go-live approved; a new go-live is needed.",
                       mapping_ids=sorted(str(c.mapping_id) for c in plan.columns),
                       rule_ids=sorted(str(r.rule_id) for r in plan.rules))
    try:
        content = blobs.get(load.file_ref)
    except BlobNotFound:
        return _failed("raw", "file_missing", f"The file is missing from the store: {load.file_ref}")
    path.write_bytes(content)
    try:
        box.load_csv(path)
    except FileUnreadable as exc:
        return _failed("raw", "not_a_table", f"DuckDB could not read the file as a CSV table: {exc}")
    rows = box.execute("SELECT count(*) FROM raw")[0][0]
    if rows == 0:
        return _failed("raw", "no_data_rows", "The file has a header and no data rows.")
    state["rows"] = rows
    state["header"] = [r[0] for r in box.execute("DESCRIBE raw") if r[0] != "_row"]
    return StageCompleted(stage="raw", rows_in=rows, rows_out=rows,
                          detail={"file_ref": load.file_ref, "columns": state["header"]})


def _curated(plan: LoadPlan, box: Sandbox, state: dict) -> RunResult:
    rows = state["rows"]
    if not plan.columns:
        return _failed("curated", "no_mapped_columns", "The feed has no current mapping, so nothing can be loaded.",
                       rows_in=rows)
    found, missing, unmapped = match_header(plan.columns, state["header"])
    if missing:
        return _failed("curated", "mapped_column_missing",
                       f"{len(missing)} mapped column(s) are not in the file: {', '.join(missing)}",
                       rows_in=rows, missing=missing)
    box.execute(curated_sql(found))
    uncast = _uncast(box, found)
    if uncast:
        listed = uncast[:FIRST_UNCAST]
        return _failed("curated", "values_do_not_cast",
                       f"{len(uncast)} value(s) do not cast to their element's type; the file is held",
                       rows_in=rows, count=len(uncast),
                       first=[{"row": r, "column": c, "value": v} for r, c, v in listed])
    state["found"] = found
    state["curated"] = box.execute(curated_rows_sql(found))
    return StageCompleted(stage="curated", rows_in=rows, rows_out=rows,
                          detail={"mapped_columns": {f.source_field: f.element_key for f in found},
                                  "unmapped_columns": unmapped})


def _uncast(box: Sandbox, found: list[Found]) -> list[tuple[int, str, str]]:
    """(row, mapped column, value) of every non-empty value that did not cast, by row and then column order."""
    bad = [(row, index, f.source_field, value) for index, f in enumerate(found)
           for row, value in box.execute(uncast_sql(f))]
    return [(row, column, value) for row, _, column, value in sorted(bad, key=lambda b: (b[0], b[1]))]


def _validated(plan: LoadPlan, box: Sandbox, state: dict) -> RunResult:
    rows = state["rows"]
    box.lock()  # from here on, nothing reaches a file or the network (phase 4 note 3)
    flags: dict[int, list[str]] = {}
    counts: dict[str, int] = {}
    for rule in plan.rules:
        try:
            flagged = box.run_rule(rule.sql)
        except (RuleFailed, RuleRefused) as exc:
            return _failed("validated", "rule_failed", f"Rule {rule.rule_key} failed: {exc}", rows_in=rows,
                           rule=rule.rule_key, error=str(exc))
        counts[rule.rule_key] = len(flagged)
        for row in flagged:
            flags.setdefault(row, []).append(rule.rule_key)
    state["flags"] = flags
    return StageCompleted(stage="validated", rows_in=rows, rows_out=rows, detail={"rules": counts})


def _standard(state: dict, records: list[StandardRecordDraft]) -> RunResult:
    keys = [f.element_key for f in state["found"]]
    for row in state["curated"]:
        records.append(StandardRecordDraft(
            row_number=int(row[0]), values={k: json_value(v) for k, v in zip(keys, row[1:], strict=True)},
            flags=sorted(state["flags"].get(int(row[0]), []))))
    return StageCompleted(stage="standard", rows_in=state["rows"], rows_out=len(records), detail=dict(PASS_THROUGH))


# ---------------------------------------------------------------- record


def record_load(session: Session, plan: LoadPlan, outcome: LoadOutcome) -> LoadRun:
    """Lock the feed row, then the load row (phase 6 plan, assumption 36), check the load is still received, and
    write. LoadNotReceived if another run finished it meanwhile: nothing is written."""
    FeedRepository(session).lock(plan.load.feed_id)
    loads = LoadRepository(session)
    if loads.lock(plan.load.id).status != "received":
        raise LoadNotReceived(f"Load {plan.load.id} was finished meanwhile; nothing is written")
    try:
        with session.begin_nested():
            load = _write(session, plan, outcome)
    except Exception as exc:
        if outcome.failed is not None:
            raise  # a failed outcome has little to write; a failure here is not an expected case
        outcome = _write_failed(outcome, exc)
        load = _write(session, plan, outcome)
    return LoadRun(load=load, stages=outcome.stages)


def _write(session: Session, plan: LoadPlan, outcome: LoadOutcome) -> Load:
    loads = LoadRepository(session)
    loads.record_stages(plan.load.id, [_stage_row(result, outcome.timings) for result in outcome.stages])
    failed = outcome.failed
    if failed is None:
        StandardRecordRepository(session).add_many(load_id=plan.load.id, feed_id=plan.load.feed_id,
                                                   records=outcome.records)
        return loads.finish(plan.load.id, status="loaded")
    return loads.finish(plan.load.id, status="failed_rolled_back", failed_stage=failed.stage,
                        failure=failed.detail.get("message") or failed.reason)


def _write_failed(outcome: LoadOutcome, exc: Exception) -> LoadOutcome:
    """The write of a loaded file failed: the completed stages stand, the standard stage failed, no records."""
    completed = [r for r in outcome.stages if r.stage != "standard"]
    now = datetime.now(UTC)
    standard = outcome.stages[-1]
    failed = _failed("standard", f"records_not_written: {exc}", f"The records could not be written: {exc}",
                     rows_in=standard.rows_in)
    timings = {**outcome.timings, "standard": (outcome.timings.get("standard", (now, now))[0], now)}
    return LoadOutcome(stages=[*completed, failed], records=[], timings=timings)


def _stage_row(result: RunResult, timings: dict[str, tuple[datetime, datetime]]) -> LoadStageDraft:
    started, finished = timings[result.stage]
    return LoadStageDraft(
        stage=result.stage, outcome=result.outcome, rows_in=result.rows_in,
        rows_out=result.rows_out if isinstance(result, StageCompleted) else 0, detail=result.detail,
        started_at=started, finished_at=finished)


__all__ = ["FailedRolledBack", "LoadOutcome", "LoadPlan", "LoadRun", "RunResult", "StageCompleted", "execute_load",
           "plan_load", "record_load", "STAGES"]
