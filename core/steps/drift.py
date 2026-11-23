"""Drift on a new delivery (phase 7.12, T4). Code only: step 12 has no model call (CLAUDE.md), and neither does this.

structure_drift()     the delivery's columns against the latest profile's: new, missing, the order (information only: the
                      engine maps by name) and a changed type. Names are compared in any case, as the engine does.
rule_rate_rises()     after the run: rules whose flagged rate rose against the last released load.
drift_actions()       the actions each finding comes with, as `profile_action` proposals with origin `rule` (migration
                      0031): made by code, decided by a person. map_new_column is for an agent to carry out (the glossary
                      builder is already asked about the new column; the mapping and rule agents follow once it is
                      selected); the rest are for a person. A changed order has no action.
record_drift_actions()  writes them (step 12's savepoint); add_drift_action() is a person adding one (T7).
drift_of_feed()       the latest delivery that drifted, its report and its actions: GET /feeds/{id}/drift.

A selected action reaches the later agents through selected_actions() like a profile action; the new go-live that follows
stores code version 2, and GET .../code/diff shows what changed from version 1.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.domain.manual import ManualProfileAction
from core.domain.profiling import ProposedAction
from core.domain.proposal import Proposal
from core.repositories.errors import ManualItemRefused, NotAnApprover
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from ports.identity.base import User


class Finding(str, Enum):
    new_column = "new_column"
    missing_column = "missing_column"
    type_change = "type_change"
    rule_rate_rise = "rule_rate_rise"


class TypeChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    was: str
    now: str


class RuleRise(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_key: str
    was: float  # flagged rows / rows in the last released load
    now: float


class DriftReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    new_columns: list[str] = []
    missing_columns: list[str] = []
    order_changed: bool = False  # information only
    order_before: list[str] = []
    order_after: list[str] = []
    type_changes: list[TypeChange] = []
    rule_rises: list[RuleRise] = []  # known only after the run

    @property
    def has_drift(self) -> bool:
        return bool(self.new_columns or self.missing_columns or self.order_changed or self.type_changes
                    or self.rule_rises)


def structure_drift(template: list[tuple[str, str]], delivery: list[tuple[str, str]]) -> DriftReport:
    """(name, DuckDB type) of the latest profile's columns against the delivery's, both in file order."""
    known = {n.lower(): (n, t) for n, t in template}
    seen = {n.lower(): (n, t) for n, t in delivery}
    new = [n for n, _ in delivery if n.lower() not in known]
    missing = [n for n, _ in template if n.lower() not in seen]
    common_before = [n for n, _ in template if n.lower() in seen]
    common_after = [n for n, _ in delivery if n.lower() in known]
    retyped = [TypeChange(column=known[k][0], was=known[k][1], now=seen[k][1])
               for k in known if k in seen and known[k][1] != seen[k][1]]
    return DriftReport(
        new_columns=new, missing_columns=missing,
        order_changed=[n.lower() for n in common_before] != [n.lower() for n in common_after],
        order_before=[n for n, _ in template], order_after=[n for n, _ in delivery], type_changes=retyped)


def rule_rate_rises(last: dict[str, tuple[int, int]], now: dict[str, tuple[int, int]]) -> list[RuleRise]:
    """{rule key: (flagged rows, rows)} of the last released load and of this one: the rules whose rate rose. A rule with
    no earlier rate (new, or no rows) is not compared."""
    rises = []
    for key, (flagged, rows) in now.items():
        before = last.get(key)
        if before is None or before[1] == 0 or rows == 0:
            continue
        was, is_now = round(before[0] / before[1], 6), round(flagged / rows, 6)
        if is_now > was:
            rises.append(RuleRise(rule_key=key, was=was, now=is_now))
    return rises


def drift_actions(report: DriftReport) -> list[ProposedAction]:
    actions = [ProposedAction(
        action="map_new_column", columns=[c], applied_by="agent",
        reason=f"{c} is new in the delivery. It is parked, not loaded; the glossary builder is asked about it, and "
               "once this is selected the mapping agent proposes its element.") for c in report.new_columns]
    actions += [ProposedAction(
        action="review_missing_column", columns=[c], applied_by="manual",
        reason=f"{c} is missing from the delivery, so it was held. Ask the source to resend it, or map the feed without it.")
        for c in report.missing_columns]
    actions += [ProposedAction(
        action="accept_type_change", columns=[t.column], applied_by="manual", params={"from": t.was, "to": t.now},
        reason=f"{t.column} was {t.was} and is {t.now} in this delivery. Check that its mapping and cleaning still fit.")
        for t in report.type_changes]
    actions += [ProposedAction(
        action="review_rule", columns=[], applied_by="manual",
        params={"rule_key": r.rule_key, "was": f"{r.was:.4f}", "now": f"{r.now:.4f}"},
        reason=f"Rule {r.rule_key} flagged {r.now:.1%} of the rows, up from {r.was:.1%} in the last released load.")
        for r in report.rule_rises]
    return actions


def _payload(action: ProposedAction, profile_id: UUID, load_id: UUID) -> dict:
    return {"profile_id": str(profile_id), "action": action.action, "columns": list(action.columns),
            "params": dict(action.params), "applied_by": action.applied_by, "reason": action.reason,
            "drift": {"load_id": str(load_id)}}


def record_drift_actions(session: Session, *, feed_id: UUID, load_id: UUID, report: DriftReport) -> list[Proposal]:
    """The report's actions as proposals of origin `rule`, on the feed's latest profile. The caller holds the feed lock."""
    profile = FeedProfileRepository(session).latest(feed_id)
    proposals = ProposalRepository(session)
    return [proposals.create_by_rule(kind="profile_action", payload=_payload(a, profile.id, load_id),
                                     required_approver_roles=["backend_team"], feed_id=feed_id)
            for a in drift_actions(report)]


def validated_rates(session: Session, load_id: UUID) -> dict[str, tuple[int, int]]:
    """{rule key: (flagged rows, rows)} of a load's validated stage; empty when it has none."""
    for stage in LoadRepository(session).stages(load_id):
        if stage.stage == "validated" and stage.outcome == "stage_completed":
            return {k: (int(v), stage.rows_in) for k, v in (stage.detail.get("rules") or {}).items()}
    return {}


def record_rule_rate_drift(session: Session, *, feed_id: UUID, load_id: UUID) -> list[Proposal]:
    """After a scheduled load finished: the rules whose flagged rate rose against the last released scheduled load.
    Proposals of origin `rule` on this delivery, like the structure findings. No earlier released load: nothing."""
    loads = [x for x in LoadRepository(session).list_for_feed(feed_id) if x.purpose == "scheduled"]
    previous = next((x for x in loads if x.id != load_id and x.status == "released"), None)
    if previous is None:
        return []
    rises = rule_rate_rises(validated_rates(session, previous.id), validated_rates(session, load_id))
    if not rises:
        return []
    return record_drift_actions(session, feed_id=feed_id, load_id=load_id, report=DriftReport(rule_rises=rises))


# ---------------------------------------------------------------- the feed's view


class DriftAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    action: str
    columns: list[str]
    params: dict[str, str]
    applied_by: str
    reason: str
    origin: str  # rule | manual
    status: str  # pending | approved | rejected


class DriftView(BaseModel):
    model_config = ConfigDict(frozen=True)

    load_id: UUID | None
    report: DriftReport
    actions: list[DriftAction]


def _is_drift_of(p: Proposal, load_id: UUID) -> bool:
    return str((p.payload.get("drift") or {}).get("load_id")) == str(load_id)


def _latest_drift_load(session: Session, feed_id: UUID):
    return next((x for x in LoadRepository(session).list_for_feed(feed_id)
                 if x.purpose == "scheduled" and (x.arrival_detail or {}).get("drift")), None)


def drift_of_feed(session: Session, feed_id: UUID) -> DriftView:
    """The latest scheduled delivery that drifted: step 12's report, plus the rules whose rate rose (from their actions),
    and every action on it (made by code or added by a person) with its status. Read only."""
    load = _latest_drift_load(session, feed_id)
    if load is None:
        return DriftView(load_id=None, report=DriftReport(), actions=[])
    report = DriftReport(**load.arrival_detail["drift"])
    found = [p for p in ProposalRepository(session).list_for_feed(feed_id, "profile_action") if _is_drift_of(p, load.id)]
    rises = [RuleRise(rule_key=p.payload["params"]["rule_key"], was=float(p.payload["params"]["was"]),
                      now=float(p.payload["params"]["now"])) for p in found if p.payload["action"] == "review_rule"]
    report = report.model_copy(update={"rule_rises": rises})
    return DriftView(load_id=load.id, report=report, actions=[
        DriftAction(proposal_id=p.id, action=p.payload["action"], columns=p.payload["columns"],
                    params=p.payload.get("params", {}), applied_by=p.payload["applied_by"], reason=p.payload["reason"],
                    origin=p.origin, status=p.status) for p in found])


def add_drift_action(session: Session, *, feed_id: UUID, user: User, item: ManualProfileAction) -> Proposal:
    """A person adds an action to the latest drift (T7): a manual proposal, no model call. ManualItemRefused: no delivery
    has drifted, or a column is not in the latest profile; NotAnApprover: the backend team decides actions."""
    if "backend_team" not in user.roles:
        raise NotAnApprover(f"A profile action is decided by backend_team; {user.id} has {user.roles}")
    from core.repositories.feeds import FeedRepository

    FeedRepository(session).lock(feed_id)
    load = _latest_drift_load(session, feed_id)
    profile = FeedProfileRepository(session).latest(feed_id)
    if load is None or profile is None:
        raise ManualItemRefused("No delivery of this feed has drifted: there is nothing to add an action to")
    have = {c.name for c in profile.columns}
    for name in item.columns:
        if name not in have:
            raise ManualItemRefused(f"The file has no column {name!r}")
    payload = {"profile_id": str(profile.id), "action": item.action, "columns": list(item.columns),
               "params": dict(item.params), "applied_by": "manual", "reason": item.reason,
               "drift": {"load_id": str(load.id)}}
    return ProposalRepository(session).create_manual(kind="profile_action", payload=payload,
                                                     required_approver_roles=["backend_team"], feed_id=feed_id,
                                                     author=user.id)
