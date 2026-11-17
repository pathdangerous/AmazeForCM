"""Write configuration the way phase 5 does (phase 5 plan, assumption 40).

From migration 0016 a mapping row must match its approved mapping proposal (same feed, same column, one of
its candidates, the next version, a registered feed), and a non-seed glossary row must be one of its
approved glossary_element proposal's options, written by the glossary owner who approved it. From 0018 a rule
matches its approved proposal the same way, and a load moves only the ways trg_loads_a_status allows (a_load,
go_live below); from 0019 a held or released load is a certified scheduled one (certified_load,
released_load, live_feed). These
helpers make a proposal of the real shape on a logged call, have the right dev user decide it, and write
the row through the repository. No trigger is bypassed. Each helper flushes; the test's session rolls back.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from core.domain.configuration import Mapping, Rule
from core.domain.glossary import GlossaryElement, GlossaryElementDraft
from core.domain.go_live import GoLive
from core.domain.load import STAGES, Load, LoadStageDraft, StandardRecordDraft
from core.domain.mapping import ElementOption, GlossaryProposalPayload, MappingProposalPayload
from core.domain.proposal import Proposal
from core.proposal_log import ProposalLog
from core.repositories.certifications import LoadCertificationRepository
from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.go_lives import GoLiveRepository
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.standard_records import StandardRecordRepository
from tests.conftest import dev_user_for
from tests.onboarded import registered_feed

GLENN = "dev-glossary-owner"


class _Answer(BaseModel):
    note: str = "synthetic"


def logged_call(session, agent_name: str, prompt_id: str):
    from ports.llm.base import LLMResult

    result = LLMResult(output=_Answer(), provider="test", model="test", model_version="0", prompt_id=prompt_id,
                       prompt_version="v1", latency_ms=0, raw_ref=None)
    return ProposalLog(session).record(agent_name=agent_name, input_ref=f"test://{prompt_id}", result=result)


def registered(session, feed_id: UUID) -> None:
    """Register the feed through the real onboarding chain if it is still a candidate."""
    if FeedRepository(session).get(feed_id).stage == "candidate":
        registered_feed(session, feed_id)


def a_registered_feed(session, code: str | None = None) -> UUID:
    code = code or f"syn_configured_{uuid.uuid4().hex[:8]}"
    feed = FeedRepository(session).create_candidate(code=code, name=code.replace("_", " ").capitalize())
    registered_feed(session, feed.id)
    return feed.id


# ---------------------------------------------------------------- mappings


def mapping_proposal(session, feed_id: UUID, source_field: str, candidates: Sequence[str], *,
                     decision: str | None = None, note: str | None = None) -> Proposal:
    """A mapping proposal of the real shape; pending unless decision is given (by the dev data steward)."""
    registered(session, feed_id)
    payload = MappingProposalPayload(source_field=source_field, column_type=None, candidates=list(candidates),
                                     recommended=candidates[0] if len(candidates) == 1 else None,
                                     reason="Synthetic reason.")
    repo = ProposalRepository(session)
    proposal = repo.create(proposal_log_id=logged_call(session, "MappingAgent", "mapping").id, kind="mapping",
                           payload=payload.model_dump(mode="json"), autonomy="A2",
                           required_approver_roles=["data_steward"], feed_id=feed_id)
    if decision is not None:
        repo.record_decision(proposal.id, user=dev_user_for("data_steward"), decision=decision, note=note)
    return repo.get(proposal.id)


def approved_mapping(session, feed_id: UUID, source_field: str, element_key: str, *,
                     candidates: Sequence[str] | None = None) -> Mapping:
    """The column's next mapping version: a proposal offering element_key (and candidates), approved, written."""
    offered = list(candidates) if candidates else [element_key]
    proposal = mapping_proposal(session, feed_id, source_field, offered, decision="approved")
    return MappingRepository(session).add_version(feed_id=feed_id, source_field=source_field,
                                                  element_key=element_key, proposal_id=proposal.id)


# ---------------------------------------------------------------- rules (phase 6, migration 0018)

RULE_SQL = "SELECT _row FROM curated WHERE close_price IS NULL"


def rule_proposal(session, feed_id: UUID, rule_key: str = "close_price_present", *, sql: str = RULE_SQL,
                  description: str = "Price is present.", decision: str | None = None,
                  note: str | None = None) -> Proposal:
    """A rule proposal of the real shape on a logged call; pending unless decision is given. 'approved' is
    decided by the steward, then the checker; 'rejected' by the steward."""
    registered(session, feed_id)
    repo = ProposalRepository(session)
    proposal = repo.create(proposal_log_id=logged_call(session, "RuleRecommenderAgent", "rule_recommender").id,
                           kind="rule", payload={"rule_key": rule_key, "sql": sql, "description": description},
                           autonomy="A2", required_approver_roles=["data_steward", "checker"], feed_id=feed_id)
    if decision == "approved":
        for role in ("data_steward", "checker"):
            repo.record_decision(proposal.id, user=dev_user_for(role), decision="approved")
    elif decision == "rejected":
        repo.record_decision(proposal.id, user=dev_user_for("data_steward"), decision="rejected",
                             note=note or "Synthetic reason.")
    return repo.get(proposal.id)


def approved_rule(session, feed_id: UUID, rule_key: str = "close_price_present", *, sql: str = RULE_SQL,
                  description: str = "Price is present.") -> Rule:
    """The rule's next version: a proposal of exactly this rule, approved by both steps, written."""
    proposal = rule_proposal(session, feed_id, rule_key, sql=sql, description=description, decision="approved")
    return RuleRepository(session).add_version(feed_id=feed_id, rule_key=rule_key, sql=sql,
                                               description=description, proposal_id=proposal.id)


# ---------------------------------------------------------------- loads and go-lives (phase 6, migration 0018)


def a_load(session, feed_id: UUID, status: str = "received", *, purpose: str = "go_live_test",
           go_live_id: UUID | None = None) -> Load:
    """A load in this status, reached the way the database allows: loaded or failed_rolled_back from received
    (with its stage rows). From 0019 only a certified scheduled load is held or released: for those two
    statuses the load is a scheduled one (purpose and go_live_id are ignored; see certified_load()).
    Registers the feed if it is a candidate."""
    registered(session, feed_id)
    if status in ("held", "released"):
        return certified_load(session, feed_id, status)
    repo = LoadRepository(session)
    load = repo.create(feed_id=feed_id, file_ref=f"local://raw/{feed_id}/f.csv", purpose=purpose,
                       go_live_id=go_live_id)
    if status == "received":
        return load
    now = datetime.now(UTC)

    def stage(name: str, outcome: str = "stage_completed") -> LoadStageDraft:
        return LoadStageDraft(stage=name, outcome=outcome, rows_in=1, rows_out=1, detail={}, started_at=now,
                              finished_at=now)

    if status == "failed_rolled_back":
        repo.record_stages(load.id, [stage("raw", "failed_rolled_back")])
        return repo.finish(load.id, status="failed_rolled_back", failed_stage="raw", failure="Synthetic.")
    repo.record_stages(load.id, [stage(name) for name in STAGES])
    loaded = repo.finish(load.id, status="loaded")
    return loaded if status == "loaded" else repo.set_status(load.id, status)


def certified_load(session, feed_id: UUID, status: str = "released", *, rows: int = 10,
                   content: bytes | None = None, file_ref: str | None = None) -> Load:
    """A scheduled load of the feed, reached the way the database allows (0019): a go-live, an arrival
    (on_time_complete), `rows` standard records, four completed stages, loaded, a certification, then released
    (no record flagged: score 100.0) or held (half flagged: score 50.0; threshold 90)."""
    gl = go_live(session, feed_id)
    repo = LoadRepository(session)
    load = repo.create(feed_id=feed_id, file_ref=file_ref or f"local://raw/{feed_id}/scheduled.csv",
                       purpose="scheduled", go_live_id=gl.id)
    repo.record_arrival(load.id, outcome="on_time_complete", detail={},
                        content_sha256=hashlib.sha256(load.id.bytes if content is None else content).hexdigest())
    flagged = 0 if status == "released" else rows // 2
    StandardRecordRepository(session).add_many(load_id=load.id, feed_id=feed_id, records=[
        StandardRecordDraft(row_number=number, values={}, flags=["synthetic_rule"] if number <= flagged else [])
        for number in range(1, rows + 1)])
    now = datetime.now(UTC)
    repo.record_stages(load.id, [LoadStageDraft(stage=name, outcome="stage_completed", rows_in=rows, rows_out=rows,
                                                detail={}, started_at=now, finished_at=now) for name in STAGES])
    repo.finish(load.id, status="loaded")
    LoadCertificationRepository(session).add(
        load_id=load.id, feed_id=feed_id, rows=rows, flagged_rows=flagged,
        trust_score=Decimal(100 * (rows - flagged) / rows).quantize(Decimal("0.1")), threshold=Decimal("90"),
        outcome="released" if status == "released" else "held_consumers_warned")
    return repo.set_status(load.id, status)


def released_load(session, feed_id: UUID) -> Load:
    """A released scheduled load of the feed (see certified_load())."""
    registered(session, feed_id)
    return certified_load(session, feed_id, "released")


def live_feed(session, code: str | None = None) -> UUID:
    """A registered feed with a go-live (its current mappings and rules pinned, none here), as a scheduled load
    needs. Returns the feed id; the go-live is GoLiveRepository(session).latest(feed_id)."""
    feed_id = a_registered_feed(session, code)
    go_live(session, feed_id)
    return feed_id


def go_live(session, feed_id: UUID, *, owner: str = "dev-data-owner", hypercare_days: int = 10) -> GoLive:
    """The feed's next go-live as the steps write it: a loaded test load, a review_summary of the current
    mappings and rules decided by the owner, then the row. The feed's registered owner must be `owner`."""
    registered(session, feed_id)
    mappings = [m.id for m in MappingRepository(session).current_for_feed(feed_id)]
    rules = [r.id for r in RuleRepository(session).current_for_feed(feed_id)]
    test_load = a_load(session, feed_id, "loaded")
    repo = ProposalRepository(session)
    proposal = repo.create(
        proposal_log_id=logged_call(session, "ReviewSummariserAgent", "review_summariser").id,
        kind="review_summary", autonomy="A1", required_approver_roles=["data_owner"], feed_id=feed_id,
        payload={"mapping_ids": [str(i) for i in mappings], "rule_ids": [str(i) for i in rules],
                 "test_load_id": str(test_load.id), "summary": "Synthetic.", "highlights": [], "risks": []})
    repo.record_decision(proposal.id, user=dev_user_for("data_owner"), decision="approved")
    return GoLiveRepository(session).add_version(
        feed_id=feed_id, proposal_id=proposal.id, approved_by=owner, mapping_ids=mappings, rule_ids=rules,
        test_load_id=test_load.id, hypercare_until=date.today() + timedelta(days=hypercare_days))


# ---------------------------------------------------------------- glossary elements


def new_element(key: str, *, name: str | None = None, domain: str = "pricing", data_type: str = "string",
                definition: str = "Synthetic definition.", aliases: Sequence[str] = ()) -> ElementOption:
    element = GlossaryElementDraft(key=key, name=name or key.replace("_", " ").capitalize(), domain=domain,
                                   data_type=data_type, definition=definition, aliases=list(aliases))
    return ElementOption(kind="new_element", key=key, element=element)


def alias(key: str, alias_: str) -> ElementOption:
    return ElementOption(kind="alias", key=key, alias=alias_)


def glossary_proposal(session, options: Sequence[ElementOption], *, feed_id: UUID | None = None,
                      column: str | None = None, outcome: str | None = None,
                      decision: str | None = None) -> Proposal:
    """A glossary_element proposal of the real shape on a registered feed; decided by Glenn if asked."""
    feed_id = feed_id or a_registered_feed(session)
    outcome = outcome or ("conflict_to_owner" if len(options) != 1 else
                          "proposed" if options[0].kind == "new_element" else "duplicate_alias")
    payload = GlossaryProposalPayload(source_field=column or f"col_{uuid.uuid4().hex[:6]}", outcome=outcome,
                                      options=list(options),
                                      recommended=None if outcome == "conflict_to_owner" else 0,
                                      reason="Synthetic reason.", run_id=uuid.uuid4())
    repo = ProposalRepository(session)
    proposal = repo.create(proposal_log_id=logged_call(session, "GlossaryBuilderAgent", "glossary_builder").id,
                           kind="glossary_element", payload=payload.model_dump(mode="json"), autonomy="A2",
                           required_approver_roles=["glossary_owner"], feed_id=feed_id)
    if decision is not None:
        repo.record_decision(proposal.id, user=dev_user_for("glossary_owner"), decision=decision)
    return repo.get(proposal.id)


def approved_element(session, option: ElementOption, *, feed_id: UUID | None = None,
                     column: str | None = None) -> GlossaryElement:
    """The option written from an approved proposal that offers it, by the glossary owner who approved it."""
    proposal = glossary_proposal(session, [option], feed_id=feed_id, column=column, decision="approved")
    repo = GlossaryRepository(session)
    repo.lock()
    return repo.add_version(proposal_id=proposal.id, created_by=GLENN, option=option)


# ---------------------------------------------------------------- a feed ready to load (phase 7, steps 15 and 20)

VOLUME_HEADER = "instr_id,trade_date,traded_qty\n"
VOLUME_RULE = "volume_over_100000"


def volume_csv(rows: int = 10, flagged: int = 0) -> str:
    """`rows` data rows, the first `flagged` over the rule's limit (so VOLUME_RULE flags them)."""
    return VOLUME_HEADER + "".join(f"syn-{n:04d},2026-11-02,{200000 if n <= flagged else 1000}\n"
                                   for n in range(1, rows + 1))


def loadable_feed(session, code: str = "syn_loadable", *, visibility: str = "open",
                  owner: str = "dev-data-owner") -> tuple[UUID, GoLive]:
    """A registered feed (source `code`, local file) with three mappings (instrument_id, price_date, volume), one
    rule (VOLUME_RULE) and a go-live pinning them: what a scheduled load of volume_csv() needs."""
    from tests.onboarded import registered_feed as register

    feed = FeedRepository(session).create_candidate(code=code, name=code.replace("_", " ").capitalize(),
                                                    source_code=code, connector="local_file",
                                                    source_ref=f"files/{code}.csv")
    register(session, feed.id, owner_id=owner, visibility=visibility)
    for field, key in (("instr_id", "instrument_id"), ("trade_date", "price_date"), ("traded_qty", "volume")):
        approved_mapping(session, feed.id, field, key)
    approved_rule(session, feed.id, VOLUME_RULE, sql="SELECT _row FROM curated WHERE volume > 100000",
                  description="Volume is at most 100000.")
    return feed.id, go_live(session, feed.id, owner=owner)


def onboarding_requirement(session, feed_id: UUID, *, title: str = "Synthetic request", elements: Sequence[str] = (),
                           terms: Sequence[str] = (), requester: str = "dev-requester"):
    """A requirement whose current choice selects the feed, onboarding."""
    from core.repositories.requirements import RequirementRepository
    from core.repositories.source_choices import SourceChoiceRepository

    requirements = RequirementRepository(session)
    requirement = requirements.create(requester_id=requester, title=title, element_keys=list(elements),
                                      terms=list(terms))
    SourceChoiceRepository(session).record_known(requirement_id=requirement.id, feed_id=feed_id,
                                                 source_code=FeedRepository(session).get(feed_id).source_code)
    requirements.advance(requirement.id, "source_selected")
    return requirements.advance(requirement.id, "onboarding")
