"""Step 02 profile: DuckDB profiles a candidate feed's latest fetched file (phase 4 plan, assumptions 5 to 13).

Outcomes (CLAUDE.md): profiled; spec_only_low_confidence. Rules execute: no model call here (the
ProfilerAgent's summary is its own step, later).

Three parts, as for the fetch (assumption 11):
- profile_plan() reads: the feed is a candidate, its latest fetched file, its catalogue entry, and the
  requirements that select it now (requirements_selecting, never feeds.requirement_id).
- The caller ends that transaction. run_profile() gets the bytes with BlobStore.get(), the only port
  method it uses (so Azure Blob needs no change), writes them to a temporary folder made for this run,
  and runs DuckDB in memory. No session. The folder is removed afterwards, also after a failure.
- record_profile() locks the feed row, checks it is still a candidate, computes coverage and writes the
  next profile version.
The file gives no data rows (a header only, not a CSV table, not a .csv, missing from the store):
spec_only_low_confidence, with the reason. The step flushes; the caller commits.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.profiling import DuplicateKey, FeedProfile, FileProfile, ProfilerInput, ProfilerOutput
from core.domain.requirement import Requirement
from core.repositories.errors import (
    FeedHasNoFile,
    FeedNotACandidate,
    FeedNotProfiled,
    ProfileAlreadySummarised,
    ProfileSuperseded,
)
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.profiling import ENGINE, NotATable, coverage_of, duplicate_key_found, profile_csv, spec_of
from core.steps.result import StepResult
from core.steps.s1_scope import RunsAgent
from core.steps.samples import samples_for_model
from ports.config import Settings
from ports.connectors.catalogue import SourceCatalogue, SourceEntry
from ports.storage.base import BlobNotFound, BlobStore

HEADER_ONLY = "The file has a header and no data rows."


class Profiled(StepResult):
    outcome: Literal["profiled"] = "profiled"
    profile: FeedProfile


class SpecOnlyLowConfidence(StepResult):
    outcome: Literal["spec_only_low_confidence"] = "spec_only_low_confidence"
    profile: FeedProfile  # the spec and coverage from it; no statistics
    detail: str  # why the file gave nothing to profile


ProfileResult = Annotated[Profiled | SpecOnlyLowConfidence, Field(discriminator="outcome")]


class ProfilePlan(BaseModel):
    """What one profile needs, read in a transaction the caller then ends."""

    model_config = ConfigDict(frozen=True)

    feed_id: UUID
    fetch_id: UUID
    blob_ref: str
    entry: SourceEntry
    requirements: list[Requirement]


class ProfileAttempt(BaseModel):
    """What DuckDB found: the file's statistics, or why there are none (detail set: spec only)."""

    model_config = ConfigDict(frozen=True)

    plan: ProfilePlan
    file: FileProfile | None = None  # a header-only file has its columns and no rows
    detail: str | None = None


def profile_plan(session: Session, *, feed_id: UUID, catalogue: SourceCatalogue) -> ProfilePlan:
    """Read only. FeedNotFound, FeedNotACandidate, FeedHasNoFile; UnknownSource if the source left the catalogue."""
    feed = FeedRepository(session).get(feed_id)
    if feed.stage != "candidate":
        raise FeedNotACandidate(f"Feed {feed.code!r} is {feed.stage}; only a candidate feed is profiled")
    fetch = SourceFetchRepository(session).latest_fetched(feed.id)
    if fetch is None:
        raise FeedHasNoFile(f"Feed {feed.code!r} has no fetched file to profile")
    entry = catalogue.get(feed.source_code or "")
    requirements = RequirementRepository(session)
    return ProfilePlan(feed_id=feed.id, fetch_id=fetch.id, blob_ref=fetch.blob_ref, entry=entry,
                       requirements=[requirements.get(r) for r in SourceChoiceRepository(session).requirements_selecting(feed.id)])


def run_profile(plan: ProfilePlan, *, blob_store: BlobStore) -> ProfileAttempt:
    """No database. BlobStore.get(), a temporary file, DuckDB in memory."""
    name = plan.blob_ref.rsplit("/", 1)[-1]
    if not name.lower().endswith(".csv"):
        return ProfileAttempt(plan=plan, detail=f"Only CSV files are profiled; {name} is not one.")
    try:
        content = blob_store.get(plan.blob_ref)
    except BlobNotFound:
        return ProfileAttempt(plan=plan, detail=f"The file is missing from the store: {plan.blob_ref}")
    with tempfile.TemporaryDirectory(prefix="amaze-profile-") as folder:
        path = Path(folder) / "file.csv"
        path.write_bytes(content)
        try:
            file = profile_csv(path)
        except NotATable as exc:
            return ProfileAttempt(plan=plan, detail=f"DuckDB could not read the file as a CSV table: {exc}")
    if file.row_count == 0:
        return ProfileAttempt(plan=plan, file=file, detail=HEADER_ONLY)
    return ProfileAttempt(plan=plan, file=file)


def record_profile(session: Session, attempt: ProfileAttempt, *, profiled_by: str) -> Profiled | SpecOnlyLowConfidence:
    """Lock the feed row (so two profiles take versions one after the other), check its stage again, write."""
    plan = attempt.plan
    feed = FeedRepository(session).lock(plan.feed_id)
    if feed.stage != "candidate":
        raise FeedNotACandidate(f"Feed {feed.code!r} became {feed.stage} during the profile; nothing is written")
    file = attempt.file
    profile = FeedProfileRepository(session).add_version(
        feed_id=feed.id, fetch_id=plan.fetch_id,
        outcome="profiled" if attempt.detail is None else "spec_only_low_confidence",
        row_count=file.row_count if file else 0, columns=file.columns if file else [],
        coverage=[coverage_of(r, plan.entry, file) for r in plan.requirements], spec=spec_of(plan.entry),
        detail=attempt.detail, engine=ENGINE, profiled_by=profiled_by)
    if attempt.detail is None:
        return Profiled(profile=profile)
    return SpecOnlyLowConfidence(profile=profile, detail=attempt.detail)


# ---------------------------------------------------------------- the ProfilerAgent's summary (assumptions 14 to 17)


class ProfileSummary(BaseModel):
    """The summary waiting for the sign-off. Not an outcome: nobody has decided anything yet."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    log_id: UUID
    profile_id: UUID
    summary: str
    recommended_outcome: str
    gaps: list[str]
    reasons: list[str]
    key_columns: list[str] = []  # proposed by the agent, checked by code
    key_reason: str | None = None
    confidence: float | None = None
    duplicate_key: DuplicateKey | None = None  # counted by code; None: no key, or no repeated key
    unknown_key_columns: list[str] = []


def summary_of(session: Session, *, feed_id: UUID, profile_id: UUID):
    """The profile_summary proposal of this profile, if one exists (one per profile)."""
    found = [p for p in ProposalRepository(session).list_for_feed(feed_id, "profile_summary")
             if p.payload.get("profile_id") == str(profile_id)]
    return found[-1] if found else None


def summary_input(session: Session, *, feed_id: UUID, settings: Settings | None = None) -> ProfilerInput:
    """Read only: the agent's input for the feed's latest profile. The caller then ends the transaction, so
    no connection is held while the model answers. settings decides whether sample values go to the model
    (SEND_SAMPLE_VALUES, D3); none given means they do not."""
    feed = FeedRepository(session).get(feed_id)
    if feed.stage != "candidate":
        raise FeedNotACandidate(f"Feed {feed.code!r} is {feed.stage}; only a candidate feed is summarised")
    profile = FeedProfileRepository(session).latest(feed_id)
    if profile is None:
        raise FeedNotProfiled(f"Feed {feed.code!r} has no profile to summarise")
    if summary_of(session, feed_id=feed_id, profile_id=profile.id) is not None:
        raise ProfileAlreadySummarised(f"Profile {profile.version} of feed {feed.code!r} already has its summary")
    requirements = RequirementRepository(session)
    fetch = next((f for f in SourceFetchRepository(session).list_for_feed(feed.id) if f.id == profile.fetch_id), None)
    return ProfilerInput(
        feed_id=feed.id, feed_code=feed.code, profile_id=profile.id, outcome=profile.outcome,
        row_count=profile.row_count, detail=profile.detail, spec=profile.spec,
        # the samples stay with the profile; the model sees them only in `samples`, and only when D3's setting is true
        columns=[c.model_copy(update={"samples": []}) for c in profile.columns],
        samples=samples_for_model(profile.columns, settings or Settings(_env_file=None, send_sample_values=False)),
        issues=profile.issues, file_ref=fetch.blob_ref if fetch else None,
        coverage=profile.coverage,
        requirement_titles={c.requirement_id: requirements.get(c.requirement_id).title for c in profile.coverage})


def key_facts(output: ProfilerOutput, *, columns: list[str], file: Path | None) -> ProfilerOutput:
    """after_answer: keep the proposed key columns that exist in the profile (the others are listed in
    unknown_key_columns), and count the duplicates of the key by DuckDB over the file. No file, no key, or a
    unique key: duplicate_key is None."""
    known = [k for k in output.key_columns if k in columns]
    unknown = [k for k in output.key_columns if k not in columns]
    duplicate = duplicate_key_found(file, known) if file is not None and known else None
    return output.model_copy(update={"key_columns": known, "unknown_key_columns": unknown, "duplicate_key": duplicate})


def _still_the_latest(session: Session, agent_input: ProfilerInput) -> None:
    """before_write: lock the feed row, then check again that it is a candidate, that the profile is still
    its latest and that nobody summarised it meanwhile."""
    feed = FeedRepository(session).lock(agent_input.feed_id)
    if feed.stage != "candidate":
        raise FeedNotACandidate(f"Feed {feed.code!r} became {feed.stage} during the summary")
    if FeedProfileRepository(session).latest(feed.id).id != agent_input.profile_id:
        raise ProfileSuperseded(f"Feed {feed.code!r} has a newer profile than the one summarised")
    if summary_of(session, feed_id=feed.id, profile_id=agent_input.profile_id) is not None:
        raise ProfileAlreadySummarised(f"The profile of feed {feed.code!r} was summarised meanwhile")


def record_summary(session: Session, *, agent: RunsAgent, agent_input: ProfilerInput,
                   blob_store: BlobStore | None = None) -> ProfileSummary:
    """The model call (its call row committed at once by Agent.run()), then the key check (after_answer: code
    drops unknown key columns and counts duplicates over the stored file, no connection held), then, with the
    feed locked and checked again, the one profile_summary proposal. A change during the call: a discard row and
    nothing else. Without a blob_store the duplicates are not counted."""

    def after_answer(output: ProfilerOutput) -> ProfilerOutput:
        names = [c.name for c in agent_input.columns]
        if blob_store is None or agent_input.file_ref is None or not any(k in names for k in output.key_columns):
            return key_facts(output, columns=names, file=None)
        try:
            content = blob_store.get(agent_input.file_ref)
        except BlobNotFound:
            return key_facts(output, columns=names, file=None)
        with tempfile.TemporaryDirectory(prefix="amaze-key-") as folder:
            path = Path(folder) / "file.csv"
            path.write_bytes(content)
            return key_facts(output, columns=names, file=path)

    run = agent.run(session, agent_input, before_write=lambda s: _still_the_latest(s, agent_input),
                    after_answer=after_answer)
    if len(run.proposals) != 1 or run.proposals[0].kind != "profile_summary":
        raise ValueError("step 02 expects exactly one profile_summary proposal from the agent")
    proposal = run.proposals[0]
    p = proposal.payload
    return ProfileSummary(proposal_id=proposal.id, log_id=run.log_id, profile_id=agent_input.profile_id,
                          summary=p["summary"], recommended_outcome=p["recommended_outcome"], gaps=p["gaps"],
                          reasons=p["reasons"], **_key_part(p))


def _key_part(payload: dict) -> dict:
    """The key fields of a summary payload (older payloads have none)."""
    return {"key_columns": payload.get("key_columns", []), "key_reason": payload.get("key_reason"),
            "confidence": payload.get("confidence"), "duplicate_key": payload.get("duplicate_key"),
            "unknown_key_columns": payload.get("unknown_key_columns", [])}
