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

from core.domain.profiling import (
    DuplicateKey,
    FeedProfile,
    FileColumn,
    FileProfile,
    GlossaryFit,
    ProfilerInput,
    ProfilerOutput,
)
from core.domain.requirement import Requirement
from core.repositories.errors import (
    FeedHasNoFile,
    FeedNotACandidate,
    FeedNotProfiled,
    NotAnApprover,
    ProfileAlreadySummarised,
    ProfileSuperseded,
    ProposalAlreadyDecided,
    WrongProposalKind,
)
from core.repositories.glossary import GlossaryRepository
from core.steps.candidates import MIN_SCORE, SIMILAR, score
from core.steps.errors import needs_a_note
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.profiling import (
    ENGINE,
    NotATable,
    coverage_of,
    duplicate_key_found,
    incomplete_records_found,
    profile_csv,
    spec_of,
)
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


def profile_plan(session: Session, *, feed_id: UUID, catalogue: SourceCatalogue, stage: str = "candidate") -> ProfilePlan:
    """Read only. FeedNotFound, FeedNotACandidate, FeedHasNoFile; UnknownSource if the source left the catalogue.
    `stage` is "registered" only for core/steps/profile_again.py (phase 7.16)."""
    feed = FeedRepository(session).get(feed_id)
    if feed.stage != stage:
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


def record_profile(session: Session, attempt: ProfileAttempt, *, profiled_by: str,
                   stage: str = "candidate") -> Profiled | SpecOnlyLowConfidence:
    """Lock the feed row (so two profiles take versions one after the other), check its stage again, write."""
    plan = attempt.plan
    feed = FeedRepository(session).lock(plan.feed_id)
    if feed.stage != stage:
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
        glossary_fit=fit_to_glossary(profile.columns, GlossaryRepository(session).list_current()),
        coverage=profile.coverage,
        requirement_titles={c.requirement_id: requirements.get(c.requirement_id).title for c in profile.coverage})


def fit_to_glossary(columns: list[FileColumn], elements: list) -> GlossaryFit:
    """T1, by code: each column against the glossary. matched: a name or alias fits (score at or above SIMILAR);
    new: nothing is close (below MIN_SCORE); unmatched: something is close, but a person must choose."""
    matched, new, unmatched = [], [], []
    for c in columns:
        best = max((score(c.name, e) for e in elements), default=0.0)
        (matched if best >= SIMILAR else unmatched if best >= MIN_SCORE else new).append(c.name)
    return GlossaryFit(matched=matched, new=new, unmatched=unmatched)


def selected_actions(session: Session, feed_id: UUID) -> list[dict]:
    """T1: the profile actions a person selected (approved), as the later agents' inputs hold them. Code, no model."""
    return [{"proposal_id": str(p.id), "action": p.payload["action"], "columns": p.payload["columns"],
             "params": p.payload["params"], "applied_by": p.payload["applied_by"], "reason": p.payload["reason"]}
            for p in ProposalRepository(session).list_for_feed(feed_id, "profile_action", "approved")]


def excluded_by_actions(session: Session, feed_id: UUID) -> set[str]:
    """The columns a selected (approved) `exclude_column` action leaves out: the later agents are not asked about them."""
    return {c for a in selected_actions(session, feed_id) if a["action"] == "exclude_column" for c in a["columns"]}


def actions_were_proposed(session: Session, feed_id: UUID) -> bool:
    """True when the feed's profile step proposed any action (selected or not): then a cleaning step is proposed only for
    a column whose action was selected. A feed that never had actions is cleaned from the facts alone, as before."""
    return any(ProposalRepository(session).list_for_feed(feed_id, "profile_action", status)
               for status in ("approved", "rejected", "pending"))


def todo_actions(session: Session, feed_id: UUID) -> list[dict]:
    """The selected actions a person said they would do themselves: the to-do list on the feed."""
    return [a for a in selected_actions(session, feed_id) if a["applied_by"] == "manual"]


class ProfileActionDecided(BaseModel):
    """Not an outcome: a person selected (approved) or rejected one profile action."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    decision: str
    action: str


def decide_profile_action(session: Session, *, proposal_id: UUID, user, decision: str,
                          note: str | None = None) -> ProfileActionDecided:
    """The backend team selects (approves) or rejects an action. An action changes no data and no configuration by
    itself: a selected one is passed to the later agents' inputs, or is a to-do for a person (rule 1)."""
    proposals = ProposalRepository(session)
    proposal = proposals.get(proposal_id)
    if proposal.kind != "profile_action":
        raise WrongProposalKind(f"Proposal {proposal_id} is a {proposal.kind} proposal, not profile_action")
    needs_a_note(decision, note)
    FeedRepository(session).lock(proposal.feed_id)
    proposal = proposals.lock(proposal_id)
    if proposal.status != "pending":
        raise ProposalAlreadyDecided(f"Proposal {proposal_id} is already {proposal.status}")
    if "backend_team" not in user.roles:
        raise NotAnApprover(f"Proposal {proposal_id} needs role backend_team; {user.id} has {user.roles}")
    proposals.record_decision(proposal_id, user=user, decision=decision, note=note)
    return ProfileActionDecided(proposal_id=proposal_id, decision=decision, action=proposal.payload["action"])


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
        # T1: facts by code, never from the model; an action naming a column the file lacks is dropped
        output = output.model_copy(update={
            "glossary_fit": agent_input.glossary_fit, "incomplete_records": None,
            "actions": [a for a in output.actions if all(c in names for c in a.columns)]})
        if blob_store is None or agent_input.file_ref is None:
            return key_facts(output, columns=names, file=None)
        try:
            content = blob_store.get(agent_input.file_ref)
        except BlobNotFound:
            return key_facts(output, columns=names, file=None)
        with tempfile.TemporaryDirectory(prefix="amaze-key-") as folder:
            path = Path(folder) / "file.csv"
            path.write_bytes(content)
            spec = agent_input.spec
            requested = [c for c, field in spec.columns.items() if field in spec.elements]
            required = list(dict.fromkeys([k for k in output.key_columns if k in names] + requested))
            output = output.model_copy(update={"incomplete_records": incomplete_records_found(path, required)})
            return key_facts(output, columns=names, file=path)

    run = agent.run(session, agent_input, before_write=lambda s: _still_the_latest(s, agent_input),
                    after_answer=after_answer)
    summaries = [p for p in run.proposals if p.kind == "profile_summary"]
    if len(summaries) != 1 or any(p.kind not in ("profile_summary", "profile_action") for p in run.proposals):
        raise ValueError("step 02 expects exactly one profile_summary proposal (and profile_action proposals)")
    proposal = summaries[0]
    p = proposal.payload
    return ProfileSummary(proposal_id=proposal.id, log_id=run.log_id, profile_id=agent_input.profile_id,
                          summary=p["summary"], recommended_outcome=p["recommended_outcome"], gaps=p["gaps"],
                          reasons=p["reasons"], **_key_part(p))


def _key_part(payload: dict) -> dict:
    """The key fields of a summary payload (older payloads have none)."""
    return {"key_columns": payload.get("key_columns", []), "key_reason": payload.get("key_reason"),
            "confidence": payload.get("confidence"), "duplicate_key": payload.get("duplicate_key"),
            "unknown_key_columns": payload.get("unknown_key_columns", [])}
