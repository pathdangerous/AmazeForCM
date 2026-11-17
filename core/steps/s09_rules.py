"""Step 09, validation rules (phase 6 plan, assumptions 3, 5 to 13; sections 5.1 to 5.3).

Outcomes (CLAUDE.md): rules_proposed, flags_most_history_returned. Not outcomes: a rule waiting for the checker
(RuleAwaitsChecker), a stored rule (RuleStored), a rejection (RuleRejected).

The model-call pattern: rules_input() reads; the API ends the transaction; record_rules() runs Agent.run(), whose
after_answer is the back-test (no connection held: the history is fetched through the feed's own connector and
every rule runs in the locked DuckDB sandbox) and whose before_write locks the feed row and checks again that the
feed's mappings and the keys of its rules are what the input held (RulesAlreadyProposed, ConfigurationChangedMeanwhile:
a discard row, nothing written). Code gives each answered rule a verdict: kept (a `rule` proposal, with its
back-test in the payload), returned (it flags more than half of the history) or refused (not a plain SELECT, an
error or timeout in the back-test, an element the feed does not map, a key that already is, is pending or was
rejected). A feed with no history is "not back-tested": its rules are kept.

decide_rule(): the steward approves (the checker is told), then the checker approves (the `rules` row, version
n+1, in the same transaction), or either rejects with a note. Lock order (assumption 36): the feed row, then the
proposal row; the next rule version is read after the feed row is locked. The step flushes; the caller commits.
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.domain.configuration import Rule
from core.domain.rules import (
    MAX_KNOWN_KEYS,
    MAX_SHARE,
    Backtest,
    Delivery,
    LoadHistoryItem,
    ProposedRule,
    RuleCandidateElement,
    RuleRecommenderInput,
    RuleRecommenderOutput,
)
from core.engine.curate import curated_sql, match_header, uncast_sql
from core.engine.sandbox import FileUnreadable, RuleFailed, RuleRefused, Sandbox, check_rule_sql
from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.errors import (
    ConfigurationChangedMeanwhile,
    FeedNotProfiled,
    FeedNotRegistered,
    NotAnApprover,
    NothingToMap,
    ProposalAlreadyDecided,
    RulesAlreadyProposed,
    WrongProposalKind,
)
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from core.steps.errors import needs_a_note
from core.steps.result import StepResult
from core.steps.rule_candidates import element_offers, feed_offers
from core.steps.s1_scope import RunsAgent
from ports.connectors.base import SourceConnector, SourceUnavailable
from ports.connectors.catalogue import SourceCatalogue
from ports.identity.base import User
from ports.notify.base import Notifier
from ports.storage.base import BlobNotFound, BlobStore

LOAD_HISTORY = 10  # the feed's own loads in the back-test (phase 7 assumption 30)

ALREADY_THERE = "The feed already has a rule or a pending proposal with this key."
WAS_REJECTED = "A steward or checker rejected a rule with this key; it is not proposed again."
TWICE = "The answer holds this rule key twice."


class ReturnedRule(BaseModel):
    """A rule that flags more than half of the history: returned to the steward, not proposed."""

    model_config = ConfigDict(frozen=True)

    rule_key: str
    sql: str
    description: str
    share: float
    flagged_rows: int
    total_rows: int
    deliveries: list[Delivery]


class RefusedRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_key: str
    sql: str
    reason: str


class RulesProposed(StepResult):
    outcome: Literal["rules_proposed"] = "rules_proposed"
    log_id: UUID
    proposal_ids: list[UUID]  # empty when every answered rule was returned or refused and none was returned
    returned: list[ReturnedRule]
    refused: list[RefusedRule]


class FlagsMostHistoryReturned(StepResult):
    outcome: Literal["flags_most_history_returned"] = "flags_most_history_returned"
    log_id: UUID
    returned: list[ReturnedRule]
    refused: list[RefusedRule]


RulesResult = Annotated[RulesProposed | FlagsMostHistoryReturned, Field(discriminator="outcome")]


class RuleAwaitsChecker(BaseModel):
    """Not an outcome: the steward approved; the checker decides next."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    rule_key: str


class RuleStored(BaseModel):
    """Not an outcome: the checker approved and the rules row exists."""

    model_config = ConfigDict(frozen=True)

    rule: Rule


class RuleRejected(BaseModel):
    """Not an outcome: a steward or a checker rejected the rule; its key is not proposed again."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    rule_key: str
    rejected_by_role: str


# ---------------------------------------------------------------- the agent's input


def _known_keys(session: Session, feed_id: UUID) -> list[str]:
    """The keys of the feed's current rules and pending rule proposals, sorted, at most MAX_KNOWN_KEYS."""
    keys = {r.rule_key for r in RuleRepository(session).current_for_feed(feed_id)}
    keys |= {p.payload["rule_key"] for p in ProposalRepository(session).list_for_feed(feed_id, "rule", "pending")}
    return sorted(keys)[:MAX_KNOWN_KEYS]


def rules_input(session: Session, *, feed_id: UUID, catalogue: SourceCatalogue) -> RuleRecommenderInput:
    """Read only. FeedNotRegistered, FeedNotProfiled, NothingToMap (no current mapping)."""
    feed = FeedRepository(session).get(feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} is {feed.stage}; only a registered feed has rules")
    profile = FeedProfileRepository(session).latest(feed_id)
    if profile is None:
        raise FeedNotProfiled(f"Feed {feed.code!r} has no profile")
    mappings = MappingRepository(session).current_for_feed(feed_id)
    if not mappings:
        raise NothingToMap(f"Feed {feed.code!r} has no current mapping to write rules for")
    stats = {c.name: c for c in profile.columns}
    glossary = GlossaryRepository(session)
    elements = []
    for mapping in mappings:
        data_type = glossary.get_current(mapping.element_key).data_type
        column = stats.get(mapping.source_field)
        elements.append(RuleCandidateElement(
            source_field=mapping.source_field, key=mapping.element_key, data_type=data_type,
            null_rate=column.null_rate if column else None, distinct_count=column.distinct_count if column else None,
            templates=element_offers(mapping.element_key, data_type)))
    entry = catalogue.get(feed.source_code) if feed.source_code else None
    return RuleRecommenderInput(
        feed_id=feed.id, feed_code=feed.code, source_name=feed.name, elements=elements,
        feed_templates=feed_offers([e.key for e in elements]), existing_keys=_known_keys(session, feed_id),
        rejected_keys=sorted(RuleRepository(session).rejected_keys(feed_id)), mapping_ids=sorted(m.id for m in mappings),
        connector=feed.connector, history=list(entry.history) if entry else [],
        load_history=[LoadHistoryItem(label=f"load {load.received_at:%Y-%m-%d}", file_ref=load.file_ref)
                      for load in LoadRepository(session).fully_loaded_scheduled(feed_id, LOAD_HISTORY)])


# ---------------------------------------------------------------- the back-test (no connection held)


def backtest_for(agent_input: RuleRecommenderInput, *, connector_factory: Callable[[str], SourceConnector],
                 rule_timeout_seconds: float,
                 blob_store: BlobStore | None = None) -> Callable[[RuleRecommenderOutput], RuleRecommenderOutput]:
    """The after_answer of Agent.run(): a copy of the answer with each rule's verdict, reason and back-test. The
    history is the source's synthetic deliveries (through its connector), then the feed's own loads (through
    BlobStore.get(); without a blob store they are not read)."""
    def run(output: RuleRecommenderOutput) -> RuleRecommenderOutput:
        return _checked(agent_input, output, connector_factory, rule_timeout_seconds, blob_store)

    return run


def _refusal(agent_input: RuleRecommenderInput, rule: ProposedRule, seen: set[str]) -> str | None:
    if rule.rule_key in seen:
        return TWICE
    if rule.rule_key in agent_input.existing_keys:
        return ALREADY_THERE
    if rule.rule_key in agent_input.rejected_keys:
        return WAS_REJECTED
    mapped = {e.key for e in agent_input.elements}
    unknown = [k for k in rule.element_keys if k not in mapped]
    if unknown or not rule.element_keys:
        return f"The rule names an element the feed does not map: {', '.join(unknown) or '(none named)'}."
    try:
        check_rule_sql(rule.sql)
    except RuleRefused as exc:
        return str(exc)
    return None


def _checked(agent_input: RuleRecommenderInput, output: RuleRecommenderOutput,
             connector_factory: Callable[[str], SourceConnector], timeout: float,
             blob_store: BlobStore | None = None) -> RuleRecommenderOutput:
    seen: set[str] = set()
    verdicts: dict[int, ProposedRule] = {}
    live: list[tuple[int, ProposedRule]] = []
    for index, rule in enumerate(output.rules):
        reason = _refusal(agent_input, rule, seen)
        seen.add(rule.rule_key)
        if reason is not None:
            verdicts[index] = rule.model_copy(update={"verdict": "refused", "reason": reason})
        else:
            live.append((index, rule))
    from_source = bool(agent_input.history) and agent_input.connector is not None
    from_loads = bool(agent_input.load_history) and blob_store is not None
    if not live or not (from_source or from_loads):
        for index, rule in live:
            verdicts[index] = rule.model_copy(update={"verdict": "kept", "backtest": Backtest(status="not_back_tested")})
    else:
        verdicts.update(_run_history(agent_input, live, connector_factory, timeout,
                                    blob_store if from_loads else None, from_source))
    return RuleRecommenderOutput(rules=[verdicts[i] for i in range(len(output.rules))])


def _run_history(agent_input: RuleRecommenderInput, live: list[tuple[int, ProposedRule]],
                 connector_factory: Callable[[str], SourceConnector], timeout: float,
                 blob_store: BlobStore | None = None, from_source: bool = True) -> dict[int, ProposedRule]:
    columns = [SimpleNamespace(source_field=e.source_field, element_key=e.key, data_type=e.data_type)
               for e in agent_input.elements]
    deliveries: dict[int, list[Delivery]] = defaultdict(list)
    failed: dict[int, str] = {}

    def one(label: str, read: Callable[[], bytes], missing: type[Exception], why_missing: str) -> None:
        try:
            content = read()
        except missing:
            _exclude(deliveries, live, label, why_missing)
            return
        ran, why = _one_delivery(content, columns, [(i, r) for i, r in live if i not in failed], timeout)
        if why is not None:
            _exclude(deliveries, live, label, why)
            return
        rows, results = ran
        for index, flagged in results.items():
            if isinstance(flagged, str):
                failed[index] = flagged
            else:
                deliveries[index].append(Delivery(label=label, rows=rows, flagged=len(flagged)))

    if from_source:
        connector = connector_factory(agent_input.connector)
        try:
            for ref in agent_input.history:
                one(ref.rsplit("/", 1)[-1].removesuffix(".csv"), lambda ref=ref: connector.fetch(ref).content,
                    SourceUnavailable, "source_unavailable")
        finally:
            connector.close()
    if blob_store is not None:  # the feed's own loads, after the source's history; no connection is held
        for item in agent_input.load_history:
            one(item.label, lambda ref=item.file_ref: blob_store.get(ref), BlobNotFound, "file_missing")
    verdicts = {}
    for index, rule in live:
        if index in failed:
            verdicts[index] = rule.model_copy(update={"verdict": "refused", "reason": failed[index]})
            continue
        included = [d for d in deliveries[index] if d.excluded is None]
        total, flagged = sum(d.rows for d in included), sum(d.flagged for d in included)
        if total == 0:
            back = Backtest(status="not_back_tested", deliveries=deliveries[index])
            verdicts[index] = rule.model_copy(update={"verdict": "kept", "backtest": back})
            continue
        share = round(flagged / total, 6)
        back = Backtest(status="ran", total_rows=total, flagged_rows=flagged, share=share, deliveries=deliveries[index])
        if share > MAX_SHARE:
            reason = f"Flags {flagged} of {total} rows ({share:.0%}) of the history: more than half."
            verdicts[index] = rule.model_copy(update={"verdict": "returned", "reason": reason, "backtest": back})
        else:
            verdicts[index] = rule.model_copy(update={"verdict": "kept", "backtest": back})
    return verdicts


def _exclude(deliveries: dict[int, list[Delivery]], live, label: str, why: str) -> None:
    for index, _ in live:
        deliveries[index].append(Delivery(label=label, rows=0, flagged=0, excluded=why))


def _one_delivery(content: bytes, columns, live, timeout: float):
    """((rows, {rule index: flagged rows, or the reason the rule failed}), None), or (None, why it is left out)."""
    box = Sandbox(timeout_seconds=timeout)
    try:
        with tempfile.TemporaryDirectory(prefix="amaze-backtest-") as folder:
            path = Path(folder) / "file.csv"
            path.write_bytes(content)
            try:
                box.load_csv(path)
            except FileUnreadable as exc:
                return None, f"DuckDB could not read the delivery: {exc}"
        header = [r[0] for r in box.execute("DESCRIBE raw") if r[0] != "_row"]
        found, missing, _ = match_header(columns, header)
        if missing:
            return None, f"{len(missing)} mapped column(s) are not in the file: {', '.join(missing)}"
        rows = box.execute("SELECT count(*) FROM raw")[0][0]
        if rows == 0:
            return None, "The delivery has no data rows."
        box.execute(curated_sql(found))
        uncast = sum(len(box.execute(uncast_sql(f))) for f in found)
        if uncast:
            return None, f"{uncast} value(s) do not cast to their element's type"
        box.lock()
        results = {}
        for index, rule in live:
            try:
                results[index] = box.run_rule(rule.sql)
            except (RuleFailed, RuleRefused) as exc:
                results[index] = str(exc)
        return (rows, results), None
    finally:
        box.close()


# ---------------------------------------------------------------- propose


def _still_as_input(session: Session, agent_input: RuleRecommenderInput) -> None:
    """before_write: the feed row locked, then the mappings and the rule keys checked against the input."""
    feed = FeedRepository(session).lock(agent_input.feed_id)
    if feed.stage != "registered":
        raise FeedNotRegistered(f"Feed {feed.code!r} became {feed.stage} while the agent answered")
    if sorted(m.id for m in MappingRepository(session).current_for_feed(feed.id)) != sorted(agent_input.mapping_ids):
        raise ConfigurationChangedMeanwhile(f"The mappings of feed {feed.code!r} changed while the agent answered")
    if (_known_keys(session, feed.id) != agent_input.existing_keys
            or sorted(RuleRepository(session).rejected_keys(feed.id)) != agent_input.rejected_keys):
        raise RulesAlreadyProposed(f"A rule of feed {feed.code!r} was proposed, stored or rejected while the agent "
                                   "answered")


def record_rules(session: Session, *, agent: RunsAgent, agent_input: RuleRecommenderInput,
                 backtest: Callable[[RuleRecommenderOutput], RuleRecommenderOutput]) -> RulesResult:
    run = agent.run(session, agent_input, before_write=lambda s: _still_as_input(s, agent_input),
                    after_answer=backtest)
    returned, refused = [], []
    for rule in run.output.rules:
        if rule.verdict == "returned":
            back = rule.backtest
            returned.append(ReturnedRule(rule_key=rule.rule_key, sql=rule.sql, description=rule.description,
                                         share=back.share, flagged_rows=back.flagged_rows, total_rows=back.total_rows,
                                         deliveries=back.deliveries))
        elif rule.verdict == "refused":
            refused.append(RefusedRule(rule_key=rule.rule_key, sql=rule.sql, reason=rule.reason or ""))
    ids = [p.id for p in run.proposals]
    if not ids and returned:
        return FlagsMostHistoryReturned(log_id=run.log_id, returned=returned, refused=refused)
    return RulesProposed(log_id=run.log_id, proposal_ids=ids, returned=returned, refused=refused)


# ---------------------------------------------------------------- the decision: steward, then checker


def decide_rule(session: Session, *, proposal_id: UUID, user: User, decision: Literal["approved", "rejected"],
                note: str | None = None, notifier: Notifier | None = None,
                checkers: Sequence[str] = ()) -> RuleAwaitsChecker | RuleStored | RuleRejected:
    proposals = ProposalRepository(session)
    proposal = proposals.get(proposal_id)
    if proposal.kind != "rule":
        raise WrongProposalKind(f"Proposal {proposal_id} is a {proposal.kind} proposal, not rule")
    needs_a_note(decision, note)  # refused input, before any lock
    feed = FeedRepository(session).lock(proposal.feed_id)  # before the proposal and the next version are read
    proposal = proposals.lock(proposal_id)
    if proposal.status != "pending":
        raise ProposalAlreadyDecided(f"Proposal {proposal_id} is already {proposal.status}")
    step = len(proposals.approvals(proposal_id)) + 1
    role = proposal.required_approver_roles[step - 1]
    if role not in user.roles:
        raise NotAnApprover(f"Step {step} of proposal {proposal_id} needs role {role}; {user.id} has {user.roles}")
    payload = proposal.payload
    proposals.record_decision(proposal_id, user=user, decision=decision, note=note)
    if decision == "rejected":
        return RuleRejected(proposal_id=proposal_id, rule_key=payload["rule_key"], rejected_by_role=role)
    if step < len(proposal.required_approver_roles):
        if notifier is not None:
            for checker in checkers:
                notifier.notify(recipient=checker, subject=f"Rule waits for the checker: {payload['rule_key']} of {feed.code}",
                                body=f"{user.display_name} approved the rule {payload['rule_key']} of feed {feed.code}. "
                                     "It needs your decision before it is stored.")
        return RuleAwaitsChecker(proposal_id=proposal_id, rule_key=payload["rule_key"])
    rule = RuleRepository(session).add_version(feed_id=proposal.feed_id, rule_key=payload["rule_key"],
                                               sql=payload["sql"], description=payload["description"],
                                               proposal_id=proposal_id)
    return RuleStored(rule=rule)
