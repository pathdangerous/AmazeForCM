"""Step 11, go-live approval (phase 6 plan, assumptions 2, 21 to 27; sections 5.1 to 5.3; phase 4 note 4).

Outcomes (CLAUDE.md): approved_scheduled, rejected_back_to_step, test_failed_held. Not an outcome: a summary waiting
for its owner (GoLiveSummary).

Prepare, in the order the API runs it (each part its own transaction, no connection held in a model call or a load):
    go_live_plan()        read: the feed is registered, has a current mapping, no summary waits, its registered owner
                          holds data_owner, and a fetched file exists. Else a named error and nothing is written
                          (except the note to the backend team when the owner lost the role).
    begin_test_load()     a received `go_live_test` load of that file (LoadInProgress if one runs).
    ... the engine (core/engine/run.py) runs it ...
    after_test_load()     a failed test load is TestFailedHeld (the backend team and the owner are told, no model
                          call, no proposal); a loaded one gives the agent's input: the facts, including the
                          unmapped columns and the rejected glossary proposals (phase 5 note 4).
    record_summary()      Agent.run(): the model call, then the locked write of the one `review_summary` proposal
                          (the pins in its payload); the feed's configuration changed meanwhile is a discard row.

decide_go_live(): only the owner named in the registration decides (NotTheFeedsOwner for another data owner;
NotAnApprover for anyone without data_owner, the named owner who lost the role included). Approve: the pinned
mapping and rule ids must still be current (else ConfigurationChangedSinceSummary: reject instead); the go-live row
and its hypercare end are written. Reject: a note and the step it goes back to (05, 06 or 09), whose role is told.
Lock order (assumption 36): the feed row, then the proposal row. The steps flush; the caller commits.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.go_live import (
    GoLive,
    MappedFact,
    PendingCounts,
    RejectedGlossaryFact,
    ReviewSummaryInput,
    RuleFact,
    StageFact,
    TestLoadFacts,
)
from core.domain.load import Load
from core.engine.run import FailedRolledBack, LoadRun
from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.errors import (
    ConfigurationChangedMeanwhile,
    ConfigurationChangedSinceSummary,
    FeedHasNoFile,
    FeedNotProfiled,
    FeedNotRegistered,
    GoLiveAlreadyProposed,
    NotAnApprover,
    NotTheFeedsOwner,
    NothingToGoLive,
    OwnerNotADataOwner,
    ProposalAlreadyDecided,
    WrongProposalKind,
)
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.go_lives import GoLiveRepository
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.repositories.transformations import TransformationRepository
from core.steps.codegen import store_code_version
from core.steps.errors import BackToStepNeeded, needs_a_note
from core.steps.result import StepResult
from core.steps.s1_scope import RunsAgent
from core.steps.s12_arrival import clear_stale_load
from ports.identity.base import User
from ports.notify.base import Notifier

BACK_TO_ROLE = {"05": "glossary_owner", "06": "data_steward", "09": "data_steward"}
BackToStep = Literal["05", "06", "09"]


class ApprovedScheduled(StepResult):
    outcome: Literal["approved_scheduled"] = "approved_scheduled"
    go_live: GoLive
    hypercare_until: date
    daily_run_utc: str  # when the daily job loads the feed from now on


class RejectedBackToStep(StepResult):
    outcome: Literal["rejected_back_to_step"] = "rejected_back_to_step"
    back_to_step: BackToStep
    note: str


class TestFailedHeld(StepResult):
    __test__ = False  # not a pytest class

    outcome: Literal["test_failed_held"] = "test_failed_held"
    load_id: UUID
    stage: str
    reason: str


GoLiveResult = Annotated[ApprovedScheduled | RejectedBackToStep | TestFailedHeld, Field(discriminator="outcome")]


class GoLivePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    feed_id: UUID
    feed_code: str
    owner_id: str
    fetch_id: UUID
    blob_ref: str


class GoLiveSummary(BaseModel):
    """Not an outcome: the owner has not decided."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    log_id: UUID
    test_load: Load


# ---------------------------------------------------------------- prepare


def go_live_plan(session: Session, *, feed_id: UUID, owner_is_data_owner: Callable[[str], bool], notifier: Notifier,
                 backend_team: Sequence[str]) -> GoLivePlan:
    """FeedNotRegistered, NothingToGoLive, GoLiveAlreadyProposed, OwnerNotADataOwner, FeedHasNoFile."""
    feed = FeedRepository(session).get(feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}; only a registered feed goes live")
    if not MappingRepository(session).current_for_feed(feed_id):
        raise NothingToGoLive(f"Feed {feed.code!r} has no current mapping: nothing to approve for loading")
    if ProposalRepository(session).list_for_feed(feed_id, "review_summary", "pending"):
        raise GoLiveAlreadyProposed(f"Feed {feed.code!r} already has a go-live summary waiting for its owner")
    registration = FeedRegistrationRepository(session).current(feed_id)
    if registration is None or not owner_is_data_owner(registration.owner_id):
        for member in backend_team:
            notifier.notify(recipient=member, subject=f"Owner cannot approve go-live: {feed.code}",
                            body=f"{feed.code} names {registration.owner_id if registration else 'no one'} as owner, "
                                 "who no longer holds the data owner role. Nobody can approve its go-live until that "
                                 "changes.")
        raise OwnerNotADataOwner(f"The owner of feed {feed.code!r} no longer holds the data_owner role")
    fetch = SourceFetchRepository(session).latest_fetched(feed_id)
    if fetch is None:
        raise FeedHasNoFile(f"Feed {feed.code!r} has no fetched file to test-load")
    return GoLivePlan(feed_id=feed_id, feed_code=feed.code, owner_id=registration.owner_id, fetch_id=fetch.id,
                      blob_ref=fetch.blob_ref)


def begin_test_load(session: Session, plan: GoLivePlan, *, stale_minutes: int, notifier: Notifier,
                    operations: Sequence[str]) -> Load:
    """A received go_live_test load of the feed's latest fetched file. A load a dead process left received for
    more than `stale_minutes` is abandoned first (step 12, phase 7 assumption 18); a younger one is
    LoadInProgress."""
    clear_stale_load(session, feed_id=plan.feed_id, stale_minutes=stale_minutes, notifier=notifier,
                     operations=operations)
    return LoadRepository(session).create(feed_id=plan.feed_id, file_ref=plan.blob_ref, purpose="go_live_test",
                                          fetch_id=plan.fetch_id)


def after_test_load(session: Session, *, plan: GoLivePlan, load_run: LoadRun, notifier: Notifier,
                    backend_team: Sequence[str]) -> TestFailedHeld | ReviewSummaryInput:
    """A failed test load: TestFailedHeld, the backend team and the owner told. A loaded one: the agent's input."""
    if load_run.load.status != "loaded":
        failure = next((s for s in load_run.stages if isinstance(s, FailedRolledBack)), None)
        stage, reason = (failure.stage, failure.reason) if failure else (load_run.load.failed_stage or "?", "unknown")
        for person in [*backend_team, plan.owner_id]:
            notifier.notify(recipient=person, subject=f"Test load failed: {plan.feed_code} ({stage})",
                            body=f"The test load of {plan.feed_code} failed at {stage}: "
                                 f"{load_run.load.failure or reason}. The feed is held; nothing was proposed.")
        return TestFailedHeld(load_id=load_run.load.id, stage=stage, reason=reason)
    return summary_input(session, feed_id=plan.feed_id, load_id=load_run.load.id)


def summary_input(session: Session, *, feed_id: UUID, load_id: UUID) -> ReviewSummaryInput:
    """Read only (phase 6 plan, assumption 23, phase 5 note 4)."""
    feed = FeedRepository(session).get(feed_id)
    registration = FeedRegistrationRepository(session).current(feed_id)
    mappings = MappingRepository(session).current_for_feed(feed_id)
    glossary = GlossaryRepository(session)
    mapped = []
    for m in mappings:
        element = glossary.get_current(m.element_key)
        mapped.append(MappedFact(source_field=m.source_field, element_key=m.element_key,
                                 element_version=element.version, data_type=element.data_type))
    profile = FeedProfileRepository(session).latest(feed_id)
    if profile is None:
        raise FeedNotProfiled(f"Feed {feed.code!r} has no profile")
    mapped_fields = {m.source_field for m in mappings}
    proposals = ProposalRepository(session)
    rejected = []
    for proposal in proposals.list_for_feed(feed_id, "glossary_element", "rejected"):
        notes = [a.note for a in proposals.approvals(proposal.id) if a.decision == "rejected"]
        rejected.append(RejectedGlossaryFact(source_field=proposal.payload.get("source_field", ""),
                                             note=notes[0] if notes else None))
    rules = RuleRepository(session).current_for_feed(feed_id)
    rule_facts = []
    for rule in rules:
        back = proposals.get(rule.proposal_id).payload.get("backtest") or {}
        rule_facts.append(RuleFact(rule_key=rule.rule_key, version=rule.version, description=rule.description,
                                   flagged_rows=back.get("flagged_rows"), total_rows=back.get("total_rows")))
    stages = LoadRepository(session).stages(load_id)
    validated = next((s for s in stages if s.stage == "validated"), None)
    return ReviewSummaryInput(
        feed_id=feed_id, feed_code=feed.code, source_name=feed.name, owner_id=registration.owner_id,
        licence=registration.licence, permitted_use=registration.permitted_use,
        redistribution=registration.redistribution, mapped=mapped,
        unmapped_columns=[c.name for c in profile.columns if c.name not in mapped_fields], rejected_glossary=rejected,
        rules=rule_facts,
        test_load=TestLoadFacts(load_id=load_id, rows=stages[0].rows_in if stages else 0,
                                stages=[StageFact(stage=s.stage, outcome=s.outcome, rows_in=s.rows_in,
                                                  rows_out=s.rows_out) for s in stages],
                                flags=dict(validated.detail.get("rules", {})) if validated else {}),
        pending=PendingCounts(glossary=len(proposals.list_for_feed(feed_id, "glossary_element", "pending")),
                              mapping=len(proposals.list_for_feed(feed_id, "mapping", "pending")),
                              rule=len(proposals.list_for_feed(feed_id, "rule", "pending"))),
        mapping_ids=sorted(m.id for m in mappings), rule_ids=sorted(r.id for r in rules),
        transformation_ids=sorted(t.id for t in TransformationRepository(session).current_for_feed(feed_id)),
        test_load_id=load_id)


def _still_as_input(session: Session, agent_input: ReviewSummaryInput) -> None:
    """before_write: the feed row locked, then the pins checked against the configuration now."""
    feed = FeedRepository(session).lock(agent_input.feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} became {feed.stage} while the agent answered")
    if (sorted(m.id for m in MappingRepository(session).current_for_feed(feed.id)) != sorted(agent_input.mapping_ids)
            or sorted(r.id for r in RuleRepository(session).current_for_feed(feed.id)) != sorted(agent_input.rule_ids)
            or sorted(t.id for t in TransformationRepository(session).current_for_feed(feed.id))
            != sorted(agent_input.transformation_ids)):
        raise ConfigurationChangedMeanwhile(f"The mappings, rules or transformations of feed {feed.code!r} changed "
                                            "while the agent answered")
    if ProposalRepository(session).list_for_feed(feed.id, "review_summary", "pending"):
        raise GoLiveAlreadyProposed(f"Feed {feed.code!r} got a go-live summary while the agent answered")


def record_summary(session: Session, *, agent: RunsAgent, agent_input: ReviewSummaryInput) -> GoLiveSummary:
    run = agent.run(session, agent_input, before_write=lambda s: _still_as_input(s, agent_input))
    (proposal,) = run.proposals
    return GoLiveSummary(proposal_id=proposal.id, log_id=run.log_id,
                         test_load=LoadRepository(session).get(agent_input.test_load_id))


# ---------------------------------------------------------------- the owner's decision


def decide_go_live(session: Session, *, proposal_id: UUID, user: User, decision: Literal["approved", "rejected"],
                   note: str | None, back_to_step: str | None, today: date, hypercare_days: int, daily_run_utc: str,
                   notifier: Notifier, backend_team: Sequence[str], operations: Sequence[str],
                   role_holders: Callable[[str], Sequence[str]]) -> ApprovedScheduled | RejectedBackToStep:
    proposals = ProposalRepository(session)
    proposal = proposals.get(proposal_id)
    if proposal.kind != "review_summary":
        raise WrongProposalKind(f"Proposal {proposal_id} is a {proposal.kind} proposal, not review_summary")
    needs_a_note(decision, note)  # refused input, before any lock
    if decision == "rejected" and back_to_step not in BACK_TO_ROLE:
        raise BackToStepNeeded("A rejection names the step it goes back to: 05, 06 or 09")
    feed = FeedRepository(session).lock(proposal.feed_id)  # before the proposal and the next version are read
    proposal = proposals.lock(proposal_id)
    if proposal.status != "pending":
        raise ProposalAlreadyDecided(f"Proposal {proposal_id} is already {proposal.status}")
    if "data_owner" not in user.roles:
        raise NotAnApprover(f"Proposal {proposal_id} needs role data_owner; {user.id} has {user.roles}")
    registration = FeedRegistrationRepository(session).current(feed.id)
    if registration is None or registration.owner_id != user.id:
        raise NotTheFeedsOwner(f"Feed {feed.code!r} is owned by "
                               f"{registration.owner_id if registration else 'no one'}, not {user.id}")
    if decision == "rejected":
        proposals.record_decision(proposal_id, user=user, decision="rejected", note=note)
        for person in role_holders(BACK_TO_ROLE[back_to_step]):
            notifier.notify(recipient=person, subject=f"Sent back to step {back_to_step}: {feed.code}",
                            body=f"{user.display_name} rejected the go-live of {feed.code}: {note}")
        return RejectedBackToStep(back_to_step=back_to_step, note=note)
    payload = proposal.payload
    current_mappings = sorted(str(m.id) for m in MappingRepository(session).current_for_feed(feed.id))
    current_rules = sorted(str(r.id) for r in RuleRepository(session).current_for_feed(feed.id))
    current_transformations = sorted(str(t.id) for t in TransformationRepository(session).current_for_feed(feed.id))
    pinned_transformations = sorted(payload.get("transformation_ids") or [])
    if (current_mappings != sorted(payload["mapping_ids"]) or current_rules != sorted(payload["rule_ids"])
            or current_transformations != pinned_transformations):
        raise ConfigurationChangedSinceSummary(f"The mappings, rules or transformations of feed {feed.code!r} "
                                               "changed after the summary was written: reject it instead")
    proposals.record_decision(proposal_id, user=user, decision="approved", note=note)
    until = today + timedelta(days=hypercare_days)
    # T3: the generated code of exactly this configuration is stored as the next code version, and the go-live pins it
    code = store_code_version(session, feed_id=feed.id, approved_by=user.id, test_load_id=UUID(payload["test_load_id"]))
    live = GoLiveRepository(session).add_version(
        feed_id=feed.id, proposal_id=proposal_id, approved_by=user.id, code_version_id=code.id,
        mapping_ids=[UUID(i) for i in payload["mapping_ids"]], rule_ids=[UUID(i) for i in payload["rule_ids"]],
        test_load_id=UUID(payload["test_load_id"]), hypercare_until=until,
        transformation_ids=[UUID(i) for i in pinned_transformations])
    for person in [*backend_team, *operations]:
        notifier.notify(recipient=person, subject=f"Feed goes live: {feed.code}",
                        body=f"{user.display_name} approved the go-live of {feed.code}. It is loaded every day at "
                             f"{daily_run_utc} UTC; every load is reported until {until.isoformat()} (hypercare).")
    return ApprovedScheduled(go_live=live, hypercare_until=until, daily_run_utc=daily_run_utc)
