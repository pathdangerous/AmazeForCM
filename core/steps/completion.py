"""The completion report before go-live (phase 7.12, T2). Read only, no model: it counts what a feed's approved
configuration, its pending items and its profile actions say, and who made each item (an agent or a person).

It is shown in the go-live step (`StepInputs.completion`) and as GET /api/amaze/feeds/{id}/completion. It never writes.
Every figure is a count over rows the steps already wrote; the sentences in `words` are built from those counts.
"""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.transformations import TransformationRepository
from core.steps.cannot_map import columns_not_loaded
from core.steps.s06_map import excluded_columns
from core.steps.s02_profile import selected_actions

ITEM_KINDS = ("profile_action", "glossary_element", "mapping", "transformation", "rule", "review_summary")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class MappedColumn(_Model):
    column: str
    element_key: str
    no_rules_yet: bool = False  # 7.22: no approved rule names its element (a column mapped late has none from the session)


class MappingFigures(_Model):
    done: int  # columns with a current mapping
    pending: int  # columns with a mapping proposal waiting for the steward
    excluded: int  # columns a person left out (not loaded)
    undecided: int  # columns with no mapping, no pending proposal, no exclusion and no glossary request on its way
    mapped_columns: list[MappedColumn]
    excluded_columns: list[str]
    waiting_for_glossary: list[str] = []  # 7.22: requested, the glossary owner or the steward has not finished (not loaded yet)


class TransformationLine(_Model):
    column: str
    steps: list[str]


class TransformationFigures(_Model):
    current: list[TransformationLine]
    pending: int


class RuleLine(_Model):
    rule_key: str
    description: str
    backtest_status: str  # ran | not_back_tested | none (an old rule with no stored back-test)
    flagged_rows: int | None
    total_rows: int | None
    made_by: str  # agent | manual


class RuleFigures(_Model):
    current: list[RuleLine]
    pending: int


class ActionFigures(_Model):
    by_agent: int  # selected actions an agent applies
    by_person: int  # selected actions a person does ("I'll do it")
    rejected: int
    pending: int


class ItemFigures(_Model):
    agent: int
    manual: int
    by_code: int = 0  # made by rules (drift actions, 0031): no model, no person
    manual_by_kind: dict[str, int]
    agent_by_kind: dict[str, int]


class CompletionReport(_Model):
    feed_id: UUID
    columns_in_file: int
    mappings: MappingFigures
    transformations: TransformationFigures
    rules: RuleFigures
    actions: ActionFigures
    items: ItemFigures
    ready_for_go_live: bool  # every column decided, nothing pending, at least one mapping
    words: list[str]  # the same facts as sentences


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _elements_with_rules(session: Session, feed_id: UUID, element_keys: list[str]) -> set[str]:
    """The mapped elements an approved rule names (its SQL, by whole word: the one match the exception code uses)."""
    from core.steps.exceptions import rule_elements

    return {key for rule in RuleRepository(session).current_for_feed(feed_id) for key in rule_elements(rule.sql, element_keys)}


def completion_report(session: Session, feed_id: UUID) -> CompletionReport:
    proposals = ProposalRepository(session)
    profile = FeedProfileRepository(session).latest(feed_id)
    in_file = [c.name for c in profile.columns] if profile else []

    current = MappingRepository(session).current_for_feed(feed_id)
    covered = _elements_with_rules(session, feed_id, [m.element_key for m in current])
    mapped = [MappedColumn(column=m.source_field, element_key=m.element_key, no_rules_yet=m.element_key not in covered)
              for m in current]
    left_out = sorted(excluded_columns(session, feed_id))
    awaiting = columns_not_loaded(session, feed_id).waiting_for_glossary
    pending_maps = {p.payload["source_field"] for p in proposals.list_for_feed(feed_id, "mapping", "pending")}
    decided = {m.column for m in mapped} | set(left_out) | pending_maps | set(awaiting)
    mappings = MappingFigures(
        done=len(mapped), pending=len(pending_maps - {m.column for m in mapped} - set(left_out)), excluded=len(left_out),
        undecided=len([c for c in in_file if c not in decided]), mapped_columns=mapped, excluded_columns=left_out,
        waiting_for_glossary=awaiting)

    transformations = TransformationFigures(
        current=[TransformationLine(column=t.source_field, steps=[s.op for s in t.steps])
                 for t in TransformationRepository(session).current_for_feed(feed_id)],
        pending=len(proposals.list_for_feed(feed_id, "transformation", "pending")))

    rule_lines = []
    for rule in RuleRepository(session).current_for_feed(feed_id):
        source = proposals.get(rule.proposal_id)
        back = source.payload.get("backtest") or {}
        rule_lines.append(RuleLine(
            rule_key=rule.rule_key, description=rule.description, backtest_status=back.get("status", "none"),
            flagged_rows=back.get("flagged_rows") if back.get("status") == "ran" else None,
            total_rows=back.get("total_rows") if back.get("status") == "ran" else None, made_by=source.origin))
    rules = RuleFigures(current=rule_lines, pending=len(proposals.list_for_feed(feed_id, "rule", "pending")))

    chosen = selected_actions(session, feed_id)
    actions = ActionFigures(
        by_agent=sum(a["applied_by"] == "agent" for a in chosen), by_person=sum(a["applied_by"] == "manual" for a in chosen),
        rejected=len(proposals.list_for_feed(feed_id, "profile_action", "rejected")),
        pending=len(proposals.list_for_feed(feed_id, "profile_action", "pending")))

    by_origin: dict[str, Counter] = {"agent": Counter(), "manual": Counter(), "rule": Counter()}
    for kind in ITEM_KINDS:
        for p in proposals.list_for_feed(feed_id, kind):
            if p.status != "superseded":  # an edit replaces its original: one item
                by_origin[p.origin][kind] += 1
    items = ItemFigures(agent=sum(by_origin["agent"].values()), manual=sum(by_origin["manual"].values()),
                        by_code=sum(by_origin["rule"].values()),
                        manual_by_kind=dict(by_origin["manual"]), agent_by_kind=dict(by_origin["agent"]))

    waiting = mappings.pending + transformations.pending + rules.pending + actions.pending
    ready = bool(mapped) and mappings.undecided == 0 and waiting == 0
    words = [
        f"{mappings.done} of {_plural(len(in_file), 'column')} mapped, {mappings.excluded} left out, "
        f"{mappings.pending} waiting for the steward, {len(awaiting)} waiting for glossary, {mappings.undecided} not decided.",
        *([f"Left out (not loaded): {', '.join(left_out)}."] if left_out else []),
        *([f"{', '.join(awaiting)}: waiting for glossary (not loaded yet)."] if awaiting else []),
        *([f"No rules yet: {', '.join(m.column for m in mapped if m.no_rules_yet)}."]
          if any(m.no_rules_yet for m in mapped) else []),
        f"{_plural(len(transformations.current), 'transformation')} approved, {transformations.pending} waiting.",
        f"{_plural(len(rules.current), 'rule')} approved, {rules.pending} waiting.",
        f"Actions: {actions.by_agent} applied by an agent, {actions.by_person} for a person to do, {actions.rejected} rejected.",
        f"Items: {items.agent} from agents, {items.manual} added by hand.",
    ]
    return CompletionReport(feed_id=feed_id, columns_in_file=len(in_file), mappings=mappings,
                            transformations=transformations, rules=rules, actions=actions, items=items,
                            ready_for_go_live=ready, words=words)
