"""Exceptions of a load (phase 7.14; plan docs/plans/phase-7.14.md, sections 2.1 to 2.3).

An exception is one flagged row of a load and one rule key in its flags (a row flagged by two rules is two). A group is
a rule key and a value pattern within one load: the shape of the values of the elements the rule names
(amaze_value_pattern(), migration 0033; core/engine/pattern.py is its twin). Groups are counted in SQL, so the number of
queries does not grow with the rows. Only a held load's groups take fixes (can_fix).

T2, the fixes. Outcomes: fixes_proposed, no_fix_proposed (per group: transformation_pending, rejected_before,
no_fix_found). The model-call pattern: exception_fix_input() reads (the held load's open groups, at most 20, and sample
rows only when SEND_SAMPLE_VALUES is true); the API ends that transaction; propose_fixes() runs Agent.run(), whose
before_write locks the feed row and checks again that the load is held and each group still open with the same count
(ExceptionGroupChangedMeanwhile: a discard row, nothing written). A transformation fix is a `transformation` proposal
marked with its group (decided by decide_transformation(), as any other); a waiver or a referral is an `exception_fix`
proposal decided by decide_exception_fix(): the steward, then for a waiver the feed's registered owner; once every step
is approved its exception_resolutions row is written in the same transaction. add_manual_fix(): a person's fix, no model
call and no log row. Lock order (CLAUDE.md): the feed row, then the proposal row. The steps flush; the caller commits.
Nothing here changes a record (rule 1).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.domain.exception_fix import MAX_AGENT_GROUPS, ExceptionFixInput, FixAction, GroupToFix
from core.domain.load import Load, LoadPurpose, LoadStatus
from core.domain.proposal import Proposal
from core.domain.transformation import TransformationDraft, TransformationProposalPayload
from core.engine.run import DUPLICATE_RULE_KEY
from core.engine.run import _key_policy as engine_key_policy  # the one place the default key policy is decided
from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.errors import (
    ExceptionGroupChangedMeanwhile,
    ItemAlreadyPending,
    LoadNotHeld,
    NotAnApprover,
    ProposalAlreadyDecided,
    TransformationAlreadyProposed,
    WrongProposalKind,
)
from core.repositories.exception_resolutions import ExceptionResolutionRepository
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feeds import FeedRepository
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.settings import FourEyesRepository
from core.repositories.transformations import TransformationRepository
from core.steps.errors import StepInputError, needs_a_note
from core.steps.result import StepResult
from core.steps.s1_scope import RunsAgent
from ports.config import Settings
from ports.identity.base import User

SAMPLES = 5
MAX_PAGE = 50
GroupState = Literal["open", "fix_pending", "waiting_for_owner", "waived", "referred", "transformation_approved"]
OPEN = "open"


class SampleRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    row_number: int
    values: dict[str, Any]


class GroupFixView(BaseModel):
    """The latest fix of a group: what it is, who made it, where it stands."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    kind: Literal["exception_fix", "transformation"]
    action: FixAction
    reason: str
    origin: str
    author: str | None
    status: str
    approvals: int
    required_approver_roles: list[str]
    steps: list[dict[str, Any]] = []


class ExceptionGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    group_key: str
    rule_key: str
    description: str | None  # the rule's current description; None for duplicate_key or a rule no longer current
    elements: list[str]  # the element keys the pattern is made of; [] means the pattern is (row)
    pattern: str
    exceptions: int
    samples: list[SampleRow]
    state: GroupState = OPEN
    fix: GroupFixView | None = None


class LoadExceptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    load_id: UUID
    feed_id: UUID
    status: LoadStatus
    can_fix: bool  # only a held load's groups take fixes (assumption 6)
    rows: int
    flagged_rows: int
    exceptions: int
    total_groups: int
    limit: int
    offset: int
    groups: list[ExceptionGroup]


class LoadExceptionCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    load_id: UUID
    received_at: datetime
    status: LoadStatus
    purpose: LoadPurpose
    rows: int
    flagged_rows: int
    exceptions: int


def group_key(rule_key: str, pattern: str) -> str:
    return hashlib.sha256(f"{rule_key}\x1f{pattern}".encode()).hexdigest()[:16]


def rule_elements(sql: str, element_keys: Sequence[str]) -> list[str]:
    """The element keys the rule's SQL names as whole words (any case), sorted. A heuristic (plan, risk 1)."""
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql)}
    return sorted(k for k in set(element_keys) if k.lower() in words)


class _Rules(BaseModel):
    elements: dict[str, list[str]]
    descriptions: dict[str, str]
    sql: dict[str, str]


def _rules(session: Session, feed_id: UUID, loaded: Sequence[str]) -> _Rules:
    rules = RuleRepository(session).current_for_feed(feed_id)
    elements = {r.rule_key: rule_elements(r.sql, loaded) for r in rules}
    policy = engine_key_policy(session, feed_id)
    if policy is not None:
        by_field = {m.source_field.lower(): m.element_key for m in MappingRepository(session).current_for_feed(feed_id)}
        elements[DUPLICATE_RULE_KEY] = sorted({by_field[c.lower()] for c in policy.key_columns if c.lower() in by_field})
    return _Rules(elements=elements, descriptions={r.rule_key: r.description for r in rules},
                  sql={r.rule_key: r.sql for r in rules})


GROUPS = text("""
WITH ex AS (
    SELECT sr.row_number, sr."values", f.rule_key
      FROM standard_records sr CROSS JOIN LATERAL unnest(sr.flags) AS f(rule_key)
     WHERE sr.load_id = :load AND (CAST(:rule AS text) IS NULL OR f.rule_key = CAST(:rule AS text))
), g AS (
    SELECT rule_key, row_number,
           amaze_value_pattern("values", ARRAY(SELECT jsonb_array_elements_text(CAST(:elements AS jsonb) -> rule_key)))
               AS pattern
      FROM ex
)
SELECT rule_key, pattern, count(*) AS n, (array_agg(row_number ORDER BY row_number))[1:%d] AS samples,
       count(*) OVER () AS total
  FROM g GROUP BY rule_key, pattern
 ORDER BY n DESC, rule_key COLLATE "C", pattern COLLATE "C"
 LIMIT :limit OFFSET :offset
""" % SAMPLES)

TOTALS = text("""
SELECT count(*), count(*) FILTER (WHERE cardinality(flags) > 0), coalesce(sum(cardinality(flags)), 0)
  FROM standard_records WHERE load_id = :load
""")

# The fixes of a load's groups: exception_fix proposals and transformation proposals marked with an exception group.
FIXES = text("""
SELECT p.id, coalesce(p.payload ->> 'group_key', p.payload -> 'exception_group' ->> 'group_key') AS group_key
  FROM proposals p
 WHERE p.feed_id = :feed AND ((p.kind = 'exception_fix' AND p.payload ->> 'load_id' = :load)
       OR (p.kind = 'transformation' AND p.payload -> 'exception_group' ->> 'load_id' = :load))
 ORDER BY p.created_at, p.id
""")


def _fixes(session: Session, load: Load) -> dict[str, list[Proposal]]:
    """Per group key, its fixes oldest first."""
    proposals = ProposalRepository(session)
    found: dict[str, list[Proposal]] = {}
    for proposal_id, key in session.execute(FIXES, {"feed": load.feed_id, "load": str(load.id)}).all():
        found.setdefault(key, []).append(proposals.get(proposal_id))
    return found


def _action(proposal: Proposal) -> FixAction:
    return "transformation" if proposal.kind == "transformation" else proposal.payload["action"]


def _state(session: Session, fixes: Sequence[Proposal]) -> tuple[GroupState, GroupFixView | None]:
    live = [p for p in fixes if p.status != "superseded"]
    if not live:
        return OPEN, None
    latest = live[-1]
    approvals = len(ProposalRepository(session).approvals(latest.id))
    view = GroupFixView(proposal_id=latest.id, kind=latest.kind, action=_action(latest),
                        reason=str(latest.payload.get("reason", "")), origin=latest.origin, author=latest.author,
                        status=latest.status, approvals=approvals,
                        required_approver_roles=list(latest.required_approver_roles),
                        steps=list(latest.payload.get("steps", [])))
    if latest.status == "rejected":
        return OPEN, view
    if latest.status == "pending":
        return ("waiting_for_owner" if approvals > 0 else "fix_pending"), view
    return {"transformation": "transformation_approved", "waive": "waived", "refer": "referred"}[view.action], view


def load_exceptions(session: Session, load_id: UUID, *, rule: str | None = None, limit: int = MAX_PAGE,
                    offset: int = 0) -> LoadExceptions:
    """Read only. LoadNotFound. Groups largest first (then rule key and pattern), `limit` at most MAX_PAGE."""
    limit = max(1, min(limit, MAX_PAGE))
    load = LoadRepository(session).get(load_id)
    rows, flagged, exceptions = session.execute(TOTALS, {"load": load_id}).one()
    rules = _rules(session, load.feed_id, _loaded_elements(session, load_id))
    found = session.execute(GROUPS, {"load": load_id, "rule": rule, "elements": json.dumps(rules.elements),
                                     "limit": limit, "offset": max(0, offset)}).all()
    wanted = sorted({n for g in found for n in g.samples})
    values = dict(session.execute(text('SELECT row_number, "values" FROM standard_records '
                                       "WHERE load_id = :load AND row_number = ANY(:rows)"),
                                  {"load": load_id, "rows": wanted}).all()) if wanted else {}
    fixes = _fixes(session, load)
    groups = []
    for g in found:
        key = group_key(g.rule_key, g.pattern)
        state, fix = _state(session, fixes.get(key, []))
        groups.append(ExceptionGroup(group_key=key, rule_key=g.rule_key, description=rules.descriptions.get(g.rule_key),
                                     elements=rules.elements.get(g.rule_key, []), pattern=g.pattern, exceptions=g.n,
                                     samples=[SampleRow(row_number=n, values=values[n]) for n in g.samples],
                                     state=state, fix=fix))
    total = found[0].total if found else _count_groups(session, load_id, rule, rules.elements, offset)
    return LoadExceptions(load_id=load.id, feed_id=load.feed_id, status=load.status, can_fix=load.status == "held",
                          rows=rows, flagged_rows=flagged, exceptions=exceptions, total_groups=total, limit=limit,
                          offset=max(0, offset), groups=groups)


def _loaded_elements(session: Session, load_id: UUID) -> list[str]:
    first = session.scalar(text('SELECT "values" FROM standard_records WHERE load_id = :load '
                                "ORDER BY row_number LIMIT 1"), {"load": load_id})
    return list(first or {})


def _count_groups(session: Session, load_id: UUID, rule: str | None, elements: dict, offset: int) -> int:
    """A page past the last group has no row to carry the total; count it (only then)."""
    if offset <= 0:
        return 0
    found = session.execute(GROUPS, {"load": load_id, "rule": rule, "elements": json.dumps(elements),
                                     "limit": 1, "offset": 0}).all()
    return found[0].total if found else 0


def feed_exceptions(session: Session, feed_id: UUID) -> list[LoadExceptionCounts]:
    """Read only. FeedNotFound. The feed's loads, newest first, each with its rows, flagged rows and exceptions."""
    FeedRepository(session).get(feed_id)
    loads = LoadRepository(session).list_for_feed(feed_id)
    if not loads:
        return []
    counts = {row[0]: row[1:] for row in session.execute(text("""
        SELECT load_id, count(*), count(*) FILTER (WHERE cardinality(flags) > 0), coalesce(sum(cardinality(flags)), 0)
          FROM standard_records WHERE load_id = ANY(:loads) GROUP BY load_id"""), {"loads": [l.id for l in loads]})}
    return [LoadExceptionCounts(load_id=l.id, received_at=l.received_at, status=l.status, purpose=l.purpose,
                                rows=counts.get(l.id, (0, 0, 0))[0], flagged_rows=counts.get(l.id, (0, 0, 0))[1],
                                exceptions=counts.get(l.id, (0, 0, 0))[2])
            for l in sorted(loads, key=lambda l: (l.received_at, l.created_at), reverse=True)]


# ---------------------------------------------------------------- T2: the fixes

NoFixReason = Literal["transformation_pending", "rejected_before", "no_fix_found"]


class UnknownExceptionGroup(StepInputError):
    """The group key names no exception group of the load."""


class NoFix(BaseModel):
    model_config = ConfigDict(frozen=True)

    group_key: str
    reason: NoFixReason
    detail: str | None = None  # the agent's own words, when it answered


class FixesProposed(StepResult):
    outcome: Literal["fixes_proposed"] = "fixes_proposed"
    log_id: UUID
    proposal_ids: list[UUID]
    no_fix: list[NoFix] = []  # the groups that got none, with why


class NoFixProposed(StepResult):
    outcome: Literal["no_fix_proposed"] = "no_fix_proposed"
    log_id: UUID | None  # None: no group was open to ask about, so no model call was made
    no_fix: list[NoFix]


FixesResult = Annotated[FixesProposed | NoFixProposed, Field(discriminator="outcome")]


class FixPlan(BaseModel):
    """What one propose call needs: the agent's input, and the groups left out of it with why (not sent to the model)."""

    model_config = ConfigDict(frozen=True)

    agent_input: ExceptionFixInput
    not_asked: list[NoFix] = []

    @property
    def groups(self) -> list[GroupToFix]:
        return self.agent_input.groups


class ExceptionFixDecided(BaseModel):
    """Not an outcome: one person's decision on an exception_fix, and where it stands now."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    status: str
    waiting_for: str | None  # the next role, while pending
    resolution_id: UUID | None = None


def _held(session: Session, load_id: UUID) -> Load:
    load = LoadRepository(session).get(load_id)
    if load.status != "held":
        raise LoadNotHeld(f"Load {load_id} is {load.status}; only a held load's exception groups take fixes")
    return load


def _all_groups(session: Session, load_id: UUID) -> list[ExceptionGroup]:
    groups, offset = [], 0
    while True:
        page = load_exceptions(session, load_id, limit=MAX_PAGE, offset=offset)
        groups += page.groups
        offset += MAX_PAGE
        if offset >= page.total_groups:
            return groups


def _rejected_before(session: Session, fixes: Sequence[Proposal]) -> list[FixAction]:
    """The fixes a person rejected for the group (a rejection by `system` is no judgement: 7.13 T4)."""
    proposals = ProposalRepository(session)
    found = [_action(p) for p in fixes if p.status == "rejected"
             and any(a.decision == "rejected" and a.decided_by != "system" for a in proposals.approvals(p.id))]
    return list(dict.fromkeys(found))


def exception_fix_input(session: Session, *, load_id: UUID, settings: Settings) -> FixPlan:
    """Read only. LoadNotFound, LoadNotHeld. The open groups of the held load, the largest first, at most 20."""
    load = _held(session, load_id)
    feed = FeedRepository(session).get(load.feed_id)
    rules = _rules(session, load.feed_id, _loaded_elements(session, load_id))
    fields = {m.element_key: m.source_field for m in MappingRepository(session).current_for_feed(load.feed_id)}
    steps = {t.source_field: [s.model_dump(mode="json") for s in t.steps]
             for t in TransformationRepository(session).current_for_feed(load.feed_id)}
    proposals, fixes = ProposalRepository(session), _fixes(session, load)
    asked, not_asked = [], []
    for group in _all_groups(session, load_id):
        if group.state != OPEN:
            continue
        mapped = {e: fields[e] for e in group.elements if e in fields}
        if len(mapped) == 1 and proposals.pending_for_column("transformation", load.feed_id,
                                                             next(iter(mapped.values()))) is not None:
            not_asked.append(NoFix(group_key=group.group_key, reason="transformation_pending"))
            continue
        if len(asked) == MAX_AGENT_GROUPS:  # shortcut: the rest wait for the next call, once these are decided
            continue
        asked.append(GroupToFix(
            group_key=group.group_key, rule_key=group.rule_key, description=group.description,
            sql=rules.sql.get(group.rule_key), elements=group.elements, pattern=group.pattern,
            exceptions=group.exceptions, source_fields=mapped,
            transformations={f: steps[f] for f in mapped.values() if f in steps},
            samples=[{e: s.values.get(e) for e in group.elements} for s in group.samples] if settings.send_sample_values
            else [], rejected_before=_rejected_before(session, fixes.get(group.group_key, []))))
    return FixPlan(agent_input=ExceptionFixInput(feed_id=feed.id, feed_code=feed.code, load_id=load.id, groups=asked),
                   not_asked=not_asked)


def _still_open(session: Session, agent_input: ExceptionFixInput) -> None:
    """before_write, under the feed lock: the load is still held and each group of the input still open, same count."""
    FeedRepository(session).lock(agent_input.feed_id)
    load = LoadRepository(session).get(agent_input.load_id)
    if load.status != "held":
        raise ExceptionGroupChangedMeanwhile(f"Load {load.id} is {load.status} now; the fixes are not written")
    now = {g.group_key: g for g in _all_groups(session, load.id)}
    for group in agent_input.groups:
        current = now.get(group.group_key)
        if current is None or current.state != OPEN or current.exceptions != group.exceptions:
            raise ExceptionGroupChangedMeanwhile(f"Group {group.group_key} ({group.rule_key}, {group.pattern}) changed "
                                                 "during the call; the fixes are not written")


def _group_of(proposal: Proposal) -> str:
    return proposal.payload.get("group_key") or proposal.payload["exception_group"]["group_key"]


def propose_fixes(session: Session, *, agent: RunsAgent, agent_input: FixPlan) -> FixesProposed | NoFixProposed:
    plan = agent_input
    if not plan.groups:
        return NoFixProposed(log_id=None, no_fix=plan.not_asked)
    run = agent.run(session, plan.agent_input, before_write=lambda s: _still_open(s, plan.agent_input))
    fixed = {_group_of(p) for p in run.proposals}
    answers = {f.group_key: f for f in run.output.fixes}
    no_fix = list(plan.not_asked)
    for group in plan.groups:
        if group.group_key in fixed:
            continue
        answer = answers.get(group.group_key)
        rejected = answer is not None and (answer.action in group.rejected_before
                                           or (answer.action == "none" and group.rejected_before))
        no_fix.append(NoFix(group_key=group.group_key, reason="rejected_before" if rejected else "no_fix_found",
                            detail=answer.reason if answer else None))
    if not run.proposals:
        return NoFixProposed(log_id=run.log_id, no_fix=no_fix)
    return FixesProposed(log_id=run.log_id, proposal_ids=[p.id for p in run.proposals], no_fix=no_fix)


def _owner(session: Session, feed_id: UUID) -> str | None:
    registration = FeedRegistrationRepository(session).current(feed_id)
    return registration.owner_id if registration else None


def _may_decide(session: Session, proposal: Proposal, user: User, step: int) -> bool:
    role = proposal.required_approver_roles[step]
    return role in user.roles and (role != "data_owner" or user.id == _owner(session, proposal.feed_id))


def decide_exception_fix(session: Session, *, proposal_id: UUID, user: User, decision: Literal["approved", "rejected"],
                         note: str | None = None) -> ExceptionFixDecided:
    """The steward, then (a waiver) the feed's registered owner. Each step its own approvals row; a person who holds
    the next role too goes on to it unless REQUIRE_FOUR_EYES is on (as approve_my_steps). Fully approved: the
    exception_resolutions row, in the same transaction. WrongProposalKind, NoteNeeded, ProposalAlreadyDecided,
    NotAnApprover."""
    proposals = ProposalRepository(session)
    proposal = proposals.get(proposal_id)
    if proposal.kind != "exception_fix":
        raise WrongProposalKind(f"Proposal {proposal_id} is a {proposal.kind} proposal, not exception_fix")
    needs_a_note(decision, note)
    FeedRepository(session).lock(proposal.feed_id)  # before the proposal (CLAUDE.md lock order)
    proposal = proposals.lock(proposal_id)
    if proposal.status != "pending":
        raise ProposalAlreadyDecided(f"Proposal {proposal_id} is already {proposal.status}")
    step = len(proposals.approvals(proposal_id))
    if not _may_decide(session, proposal, user, step):
        role = proposal.required_approver_roles[step]
        who = "the feed's registered owner" if role == "data_owner" else f"role {role}"
        raise NotAnApprover(f"Proposal {proposal_id} needs {who} for its next step; {user.id} may not decide it")
    proposals.record_decision(proposal_id, user=user, decision=decision, note=note)
    four_eyes = FourEyesRepository(session).current()
    while decision == "approved" and not four_eyes:
        proposal = proposals.get(proposal_id)
        step = len(proposals.approvals(proposal_id))
        if proposal.status != "pending" or not _may_decide(session, proposal, user, step):
            break
        proposals.record_decision(proposal_id, user=user, decision="approved", note=note)
    proposal = proposals.get(proposal_id)
    resolution = None
    if proposal.status == "approved":
        resolution = ExceptionResolutionRepository(session).record(proposal, decided_by=user.id).id
    waiting = (proposal.required_approver_roles[len(proposals.approvals(proposal_id))]
               if proposal.status == "pending" else None)
    return ExceptionFixDecided(proposal_id=proposal_id, status=proposal.status, waiting_for=waiting,
                               resolution_id=resolution)


def add_manual_fix(session: Session, *, load_id: UUID, group_key: str, action: Literal["transformation", "waive", "refer"],
                   reason: str, user: User, steps: Sequence[dict[str, Any]] | None = None) -> Proposal:
    """A person's fix for one open group of a held load: no model call, no log row (rule 4 logs model calls; 0028).
    NotAnApprover (not a data steward), LoadNotHeld, UnknownExceptionGroup, ItemAlreadyPending (a fix waits),
    TransformationAlreadyProposed, ManualFixRefused (a blank reason, steps for a group of several columns)."""
    if "data_steward" not in user.roles:
        raise NotAnApprover(f"A fix is added by a data steward; {user.id} has {user.roles}")
    if not reason.strip():
        raise ManualFixRefused("A fix needs a reason")
    load = LoadRepository(session).get(load_id)
    FeedRepository(session).lock(load.feed_id)
    load = _held(session, load_id)
    group = next((g for g in _all_groups(session, load_id) if g.group_key == group_key), None)
    if group is None:
        raise UnknownExceptionGroup(f"Load {load_id} has no exception group {group_key}")
    if group.state != OPEN:
        raise ItemAlreadyPending(f"Group {group_key} is {group.state}: decide its fix first")
    proposals = ProposalRepository(session)
    ref = {"load_id": str(load.id), "group_key": group.group_key, "rule_key": group.rule_key, "pattern": group.pattern}
    if action == "transformation":
        fields = {m.element_key: m.source_field for m in MappingRepository(session).current_for_feed(load.feed_id)}
        mapped = [(e, fields[e]) for e in group.elements if e in fields]
        if len(mapped) != 1 or not steps:
            raise ManualFixRefused("A transformation fix is for a group of one mapped column, with its steps")
        element, field = mapped[0]
        if proposals.pending_for_column("transformation", load.feed_id, field) is not None:
            raise TransformationAlreadyProposed(f"Column {field!r} already has a pending transformation")
        checked = TransformationDraft(feed_id=load.feed_id, source_field=field, steps=list(steps))
        payload = TransformationProposalPayload(source_field=field, element_key=element, steps=checked.steps,
                                                reason=reason.strip(), confidence=1.0).model_dump(mode="json")
        return proposals.create_manual(kind="transformation", payload={**payload, "exception_group": ref},
                                       required_approver_roles=["data_steward"], feed_id=load.feed_id, author=user.id)
    roles = ["data_steward", "data_owner"] if action == "waive" else ["data_steward"]
    return proposals.create_manual(kind="exception_fix", feed_id=load.feed_id, author=user.id,
                                   required_approver_roles=roles,
                                   payload={**ref, "element_keys": group.elements, "action": action,
                                            "reason": reason.strip()})


class ManualFixRefused(StepInputError):
    """A person's fix is not valid as given (a blank reason; steps for a group that is not one mapped column)."""
