"""What the backend team sees about one feed (phase 4 plan, sections 9 and 10). Read only.

The source and its conditions; each requirement that selects the feed now, with the conditions its
requester accepted (note 4: where access is restricted knowingly); the latest file; the latest profile
with its undeclared and missing columns, and the history; the ProfilerAgent's summary; the sign-offs; the
registration; and the next action.

Phase 5 (plan, sections 9 and 10): the feed's latest glossary run, and for a registered feed one row per
column of its latest profile (mapping_table). configuration_proposals() lists the glossary_element and
mapping proposals for the Glossary and Feed views.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.domain.configuration import Mapping
from core.domain.feed import Feed, FeedRegistration
from core.domain.go_live import GoLive
from core.domain.load import Load, LoadCertification, LoadStage
from core.domain.mapping import GlossaryRun
from core.domain.product import Product
from core.domain.profiling import FeedProfile, FeedSignoff
from core.domain.proposal import Approval
from core.domain.sourcing import SourceFetch, SourceSummary
from core.repositories.certifications import LoadCertificationRepository
from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feed_signoffs import FeedSignoffRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.glossary_runs import GlossaryRunRepository
from core.repositories.go_lives import GoLiveRepository
from core.repositories.loads import LoadRepository
from core.repositories.products import ProductRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.steps.candidates import candidate_list, new_columns
from core.steps.s02_profile import ProfileSummary, summary_of
from ports.connectors.catalogue import SourceCatalogue, SourceConditions, UnknownSource

FeedAction = Literal["profile", "summarise", "sign_off", "register"]
CONFIGURATION_KINDS = ("glossary_element", "mapping")


class FeedRequirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str
    status: str
    requester_id: str
    basis: str | None
    accepted_conditions: SourceConditions | None  # None for a known source: Amaze holds the agreement


class ProfileOnPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    outcome: str
    at: datetime


WhyNone = Literal["with_glossary_builder", "rejected_by_glossary_owner", "type_does_not_fit"]


class PendingMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    candidates: list[str]
    recommended: str | None  # None: a tie, the steward must choose
    reason: str


class ColumnMapping(BaseModel):
    """One column of a registered feed's latest profile, as the Mapping table shows it (phase 5 plan, section 10)."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str | None
    source_says: str | None  # the field the spec declares for this column
    mapped_to: str | None
    mapped_version: int | None
    pending: PendingMapping | None
    why_none: WhyNone | None  # only when neither mapped nor pending; None: nothing proposed yet
    versions: list[Mapping]


class ProposalOnPage(BaseModel):
    """A glossary_element or mapping proposal as the Glossary and Feed views show it."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    kind: str
    status: str
    created_at: datetime
    decided_at: datetime | None
    feed_id: UUID
    feed_code: str
    feed_name: str
    column: str
    payload: dict[str, Any]  # the options (glossary) or the candidates (mapping)
    reason: str  # the agent's
    required_approver_roles: list[str]
    decisions: list[Approval]


class FeedPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    feed: Feed
    source: SourceSummary | None
    requirements: list[FeedRequirement]
    last_file: SourceFetch | None
    profile: FeedProfile | None  # the latest
    undeclared_columns: list[str]  # in the file, not declared by the spec
    missing_columns: list[str]  # declared by the spec, not in the file
    profiles: list[ProfileOnPage]
    summary: ProfileSummary | None  # of the latest profile
    summary_status: str | None
    signoffs: list[FeedSignoff]
    registration: FeedRegistration | None
    next_action: FeedAction | None
    glossary_run: GlossaryRun | None  # phase 5: the latest
    columns: list[ColumnMapping]  # phase 5: a registered feed's mapping table; empty before registration
    rules: RulesOnPage  # phase 6
    go_live: GoLiveOnPage
    loads: list[LoadOnPage]  # the latest LOADS_ON_PAGE, newest first
    product: Product | None = None  # phase 7: the product made at the feed's first release
    parked_columns: list[str] = []  # phase 7: step 12's new columns, not mapped yet


class RuleOnPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    rule_key: str
    version: int
    description: str
    sql: str
    proposal_id: UUID
    created_at: datetime
    backtest: dict[str, Any] | None  # of the proposal the rule came from


class RulesOnPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    current: list[RuleOnPage]
    pending: list[ProposalOnPage]  # rule proposals waiting for the steward or the checker, newest first
    rejected_keys: list[str]  # not proposed again


class GoLiveOnPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    latest: GoLive | None
    hypercare_active: bool
    pending_summary: ProposalOnPage | None  # the review_summary waiting for the feed's owner


class LoadOnPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    purpose: str
    status: str
    received_at: datetime
    finished_at: datetime | None
    failed_stage: str | None
    failure: str | None
    go_live_id: UUID | None
    stages: list[LoadStage]
    # Phase 7: step 12's arrival and step 15's certification (None for a go-live test load, and before they ran)
    arrival_outcome: str | None = None
    arrival_detail: dict[str, Any] | None = None
    certification: LoadCertification | None = None


LOADS_ON_PAGE = 10


def load_on_page(session: Session, load: Load) -> LoadOnPage:
    return LoadOnPage(id=load.id, purpose=load.purpose, status=load.status, received_at=load.received_at,
                      finished_at=load.finished_at, failed_stage=load.failed_stage, failure=load.failure,
                      go_live_id=load.go_live_id, stages=LoadRepository(session).stages(load.id),
                      arrival_outcome=load.arrival_outcome, arrival_detail=load.arrival_detail,
                      certification=LoadCertificationRepository(session).for_load(load.id))


def parked_columns(session: Session, feed_id: UUID) -> list[str]:
    """Columns step 12 found in a delivery that the feed did not have, which no current mapping names yet (they
    wait for the glossary builder, the mapping step and a new go-live)."""
    profiles = FeedProfileRepository(session).history(feed_id)
    mapped = {m.source_field for m in MappingRepository(session).current_for_feed(feed_id)}
    seen: set[str] = set()
    parked: list[str] = []
    for profile in profiles:
        names = [c.name for c in profile.columns]
        if profile.profiled_by == "step_12":
            parked += [n for n in names if n not in seen and n not in mapped and n not in parked]
        seen |= set(names)
    return parked


def _rules_on_page(session: Session, feed: Feed) -> RulesOnPage:
    proposals, rules = ProposalRepository(session), RuleRepository(session)
    current = [RuleOnPage(id=r.id, rule_key=r.rule_key, version=r.version, description=r.description, sql=r.sql,
                          proposal_id=r.proposal_id, created_at=r.created_at,
                          backtest=proposals.get(r.proposal_id).payload.get("backtest"))
               for r in rules.current_for_feed(feed.id)]
    pending = sorted(proposals.list_for_feed(feed.id, "rule", status="pending"), key=lambda p: (p.created_at, str(p.id)),
                     reverse=True)
    return RulesOnPage(current=current, pending=[proposal_on_page(session, p.id) for p in pending],
                       rejected_keys=sorted(rules.rejected_keys(feed.id)))


def _go_live_on_page(session: Session, feed: Feed, today: date) -> GoLiveOnPage:
    latest = GoLiveRepository(session).latest(feed.id)
    waiting = ProposalRepository(session).list_for_feed(feed.id, "review_summary", status="pending")
    return GoLiveOnPage(latest=latest, hypercare_active=latest is not None and today <= latest.hypercare_until,
                        pending_summary=proposal_on_page(session, waiting[-1].id) if waiting else None)


def _why_none(column, *, last_glossary, with_builder: set[str], glossary) -> WhyNone | None:
    if column.name in with_builder or (last_glossary is not None and last_glossary.status == "pending"):
        return "with_glossary_builder"
    if last_glossary is not None and last_glossary.status == "rejected":
        return "rejected_by_glossary_owner"
    candidates = candidate_list(column.name, column.type, glossary)
    if candidates and not any(c.type_fits for c in candidates):
        return "type_does_not_fit"
    return None


def mapping_table(session: Session, feed: Feed, profile: FeedProfile | None) -> list[ColumnMapping]:
    """Read only. Per column: the current mapping, the pending proposal, or why there is none."""
    if feed.stage != "registered" or profile is None:
        return []
    mappings, proposals = MappingRepository(session), ProposalRepository(session)
    current = {m.source_field: m for m in mappings.current_for_feed(feed.id)}
    pending = {p.payload["source_field"]: p for p in proposals.list_for_feed(feed.id, "mapping", status="pending")}
    last_glossary = {p.payload["source_field"]: p for p in proposals.list_for_feed(feed.id, "glossary_element")}
    run = GlossaryRunRepository(session).active(feed.id)
    with_builder = ({c.name for c in new_columns(profile.columns, profile.spec, sent_back=run.columns)}
                    if run is not None else set())
    glossary = GlossaryRepository(session).list_current()
    rows = []
    for column in profile.columns:
        mapped, waiting = current.get(column.name), pending.get(column.name)
        why = None
        if mapped is None and waiting is None:
            why = _why_none(column, last_glossary=last_glossary.get(column.name), with_builder=with_builder,
                            glossary=glossary)
        rows.append(ColumnMapping(
            name=column.name, type=column.type, source_says=profile.spec.columns.get(column.name),
            mapped_to=mapped.element_key if mapped else None, mapped_version=mapped.version if mapped else None,
            pending=None if waiting is None else PendingMapping(
                proposal_id=waiting.id, candidates=waiting.payload["candidates"],
                recommended=waiting.payload["recommended"], reason=waiting.payload["reason"]),
            why_none=why, versions=mappings.history(feed.id, column.name) if mapped else []))
    return rows


def configuration_proposals(session: Session, *, kind: str | None = None, status: str | None = None,
                            feed_id: UUID | None = None) -> list[ProposalOnPage]:
    """Read only. The glossary_element and mapping proposals, newest first, with their feed and decisions."""
    proposals = ProposalRepository(session)
    found = [p for k in ((kind,) if kind else CONFIGURATION_KINDS) for p in proposals.list(status=status, kind=k)
             if feed_id is None or p.feed_id == feed_id]
    found.sort(key=lambda p: (p.created_at, str(p.id)), reverse=True)
    return [proposal_on_page(session, p.id) for p in found]


def proposal_on_page(session: Session, proposal_id: UUID) -> ProposalOnPage:
    proposals = ProposalRepository(session)
    p = proposals.get(proposal_id)
    feed = FeedRepository(session).get(p.feed_id)
    return ProposalOnPage(
        id=p.id, kind=p.kind, status=p.status, created_at=p.created_at, decided_at=p.decided_at, feed_id=feed.id,
        feed_code=feed.code, feed_name=feed.name,
        column=p.payload.get("source_field") or p.payload.get("rule_key") or "", payload=p.payload,
        reason=p.payload.get("reason", ""), required_approver_roles=p.required_approver_roles,
        decisions=proposals.approvals(p.id))


def feed_page(session: Session, feed: Feed, catalogue: SourceCatalogue) -> FeedPage:
    try:
        source = SourceSummary.of(catalogue.get(feed.source_code)) if feed.source_code else None
    except UnknownSource:
        source = None
    choices, requirements = SourceChoiceRepository(session), RequirementRepository(session)
    rows = []
    for requirement_id in choices.requirements_selecting(feed.id):
        requirement, choice = requirements.get(requirement_id), choices.current(requirement_id)
        rows.append(FeedRequirement(id=requirement.id, title=requirement.title, status=requirement.status,
                                    requester_id=requirement.requester_id, basis=choice.basis,
                                    accepted_conditions=choice.accepted_conditions))
    profiles = FeedProfileRepository(session).history(feed.id)
    latest = profiles[-1] if profiles else None
    undeclared = missing = []
    if latest is not None:
        in_file = [c.name for c in latest.columns]
        undeclared = [n for n in in_file if n not in latest.spec.columns]
        missing = [n for n in latest.spec.columns if n not in in_file] if in_file else []
    proposal = summary_of(session, feed_id=feed.id, profile_id=latest.id) if latest else None
    summary = None if proposal is None else ProfileSummary(
        proposal_id=proposal.id, log_id=proposal.proposal_log_id, profile_id=latest.id, **{
            k: proposal.payload[k] for k in ("summary", "recommended_outcome", "gaps", "reasons")})
    signoffs = FeedSignoffRepository(session).history(feed.id)
    last_file = SourceFetchRepository(session).latest(feed.id)
    has_file = SourceFetchRepository(session).latest_fetched(feed.id) is not None

    next_action: FeedAction | None = None
    if feed.stage == "candidate" and has_file:
        if latest is None:
            next_action = "profile"
        elif proposal is None:
            next_action = "summarise"
        elif proposal.status == "pending":
            next_action = "sign_off"
        elif signoffs and signoffs[-1].profile_id == latest.id and signoffs[-1].outcome != "rejected":
            next_action = "register"
    return FeedPage(
        feed=feed, source=source, requirements=rows, last_file=last_file, profile=latest,
        undeclared_columns=undeclared, missing_columns=missing,
        profiles=[ProfileOnPage(version=p.version, outcome=p.outcome, at=p.created_at) for p in profiles],
        summary=summary, summary_status=proposal.status if proposal else None, signoffs=signoffs,
        registration=FeedRegistrationRepository(session).current(feed.id), next_action=next_action,
        glossary_run=GlossaryRunRepository(session).latest(feed.id), columns=mapping_table(session, feed, latest),
        rules=_rules_on_page(session, feed), go_live=_go_live_on_page(session, feed, date.today()),
        loads=[load_on_page(session, load) for load in LoadRepository(session).list_for_feed(feed.id)[:LOADS_ON_PAGE]],
        product=ProductRepository(session).for_feed(feed.id), parked_columns=parked_columns(session, feed.id))


FeedPage.model_rebuild()  # its phase 6 sections are declared below it
