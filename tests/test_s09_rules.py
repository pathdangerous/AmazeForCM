"""Step 09, validation rules (phase 6 plan, assumptions 5 to 13; section 5.3).

Outcomes (CLAUDE.md): rules_proposed, flags_most_history_returned. The agent proposes rules over the table
`curated`; code back-tests each on the synthetic history of the source and keeps, returns or refuses it; the
steward approves, then the checker; only then is there a `rules` row.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from agents.rule_recommender import RuleRecommenderAgent
from core.domain.profiling import FileColumn, SourceSpec
from core.repositories.configuration import RuleRepository
from core.repositories.errors import (
    NotAnApprover,
    NothingToMap,
    ProposalAlreadyDecided,
    RulesAlreadyProposed,
    WrongProposalKind,
)
from core.repositories.feeds import FeedRepository
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.tables import ProposalLogDiscardRow, ProposalLogEntry
from core.steps.errors import NoteNeeded
from core.steps.s09_rules import (
    FlagsMostHistoryReturned,
    RuleAwaitsChecker,
    RuleRejected,
    RulesProposed,
    RuleStored,
    backtest_for,
    decide_rule,
    record_rules,
    rules_input,
)
from ports.config import Settings
from ports.connectors import get_connector
from ports.connectors.base import FetchedFile, SourceUnavailable
from ports.connectors.catalogue import SourceCatalogue
from ports.notify.inapp import InAppNotifier, NotificationRow
from ports.storage.local import LocalBlobStore
from tests.configured import approved_mapping, approved_rule, rule_proposal
from tests.conftest import dev_user_for
from tests.onboarded import registered_feed
from tests.stand_in_agents import FixedProvider
from tests.step_chains import end_read, propose_rules

CODE = "syn_exchange_volumes"
COLUMNS = [FileColumn(name="instr_id", type="VARCHAR", null_count=0, null_rate=0.0, distinct_count=10),
           FileColumn(name="trade_date", type="DATE", null_count=0, null_rate=0.0, distinct_count=1),
           FileColumn(name="traded_qty", type="BIGINT", null_count=0, null_rate=0.0, distinct_count=10)]
SPEC = SourceSpec(source_code=CODE, elements=["instrument_id", "price_date", "volume"], other_fields=[],
                  columns={"instr_id": "instrument_id", "trade_date": "price_date", "traded_qty": "volume"})
SQL_RULE = "SELECT _row FROM curated WHERE \"volume\" > 1000000"


@pytest.fixture
def catalogue():
    return SourceCatalogue.load(Settings(_env_file=None).source_catalogue)


@pytest.fixture
def world(db_session, glossary, tmp_path, catalogue):
    feed = FeedRepository(db_session).create_candidate(
        code=CODE, name="Exchange daily volumes", source_code=CODE, connector="local_file",
        source_ref="files/exchange_volumes.csv")
    registered_feed(db_session, feed.id, spec=SPEC, columns=COLUMNS)
    for field, key in (("instr_id", "instrument_id"), ("trade_date", "price_date"), ("traded_qty", "volume")):
        approved_mapping(db_session, feed.id, field, key)
    settings = Settings(_env_file=None)
    return SimpleNamespace(feed=feed.id, session=db_session, catalogue=catalogue, blobs=LocalBlobStore(tmp_path / "b"),
                           connectors=lambda name: get_connector(name, settings))


def agent_of(world, output=None):
    from ports.llm.mock import MockProvider

    provider = MockProvider() if output is None else FixedProvider(output)
    return RuleRecommenderAgent(provider=provider, blob_store=world.blobs)


def propose(world, output=None, **kw):
    return propose_rules(world.session, feed_id=world.feed, agent=agent_of(world, output), catalogue=world.catalogue,
                         connector_factory=kw.pop("connectors", world.connectors), blob_store=world.blobs, **kw)


def answer(*rules):
    return {"rules": [{"rule_key": k, "description": f"{k} holds.", "sql": sql, "element_keys": ["volume"]}
                      for k, sql in rules]}


def pending(world):
    return ProposalRepository(world.session).list_for_feed(world.feed, "rule", status="pending")


# ---------------------------------------------------------------- the agent's input (note 3, assumption 7)


def test_the_rule_input_holds_candidates_not_the_glossary_or_every_rule(world):
    """Phase 5 note 3: the elements the feed maps, at most 5 templates each, the keys (not the SQL) of at most 20
    current and pending rules; no glossary, no other feed's rules."""
    for n in range(22):
        approved_rule(world.session, world.feed, f"old_rule_{n:02d}", sql=f"SELECT _row FROM curated WHERE {n} = -1")
    agent_input = rules_input(world.session, feed_id=world.feed, catalogue=world.catalogue)
    assert [e.key for e in agent_input.elements] == ["instrument_id", "price_date", "volume"]
    assert all(len(e.templates) <= 5 for e in agent_input.elements)
    assert [t.rule_key for t in agent_input.feed_templates] == ["one_row_per_instrument_id_and_price_date"]
    assert len(agent_input.existing_keys) == 20 and agent_input.existing_keys == sorted(agent_input.existing_keys)
    text = agent_input.model_dump_json()
    assert "WHERE 3 = -1" not in text  # the SQL of the feed's own rules is not sent
    assert "close_price" not in text and "isin" not in text  # nor the glossary
    assert agent_input.connector == "local_file" and len(agent_input.history) == 5
    assert [(e.source_field, e.null_rate) for e in agent_input.elements][2] == ("traded_qty", 0.0)


def test_a_feed_with_no_current_mapping_has_no_rules_to_propose(db_session, glossary, catalogue):
    feed = FeedRepository(db_session).create_candidate(code="syn_unmapped", name="Unmapped")
    registered_feed(db_session, feed.id)
    with pytest.raises(NothingToMap, match="mapping"):
        rules_input(db_session, feed_id=feed.id, catalogue=catalogue)


# ---------------------------------------------------------------- the feed's own loads in the history (phase 6 note 1)

LOADED_HEADER = "instr_id,trade_date,traded_qty\n"


def a_scheduled_load(world, status, rows_text, name):
    from tests.configured import certified_load

    ref = f"raw/syn_exchange_volumes/{name}.csv"
    world.blobs.put(ref, (LOADED_HEADER + rows_text).encode())
    return certified_load(world.session, world.feed, status, file_ref=ref)


def test_the_back_test_includes_the_feeds_loaded_scheduled_loads(world):
    """Phase 7 assumption 30: the newest scheduled loads that completed all four stages (loaded, held, released),
    read through BlobStore.get() after the catalogue's history; a failed or abandoned load is not included."""
    released = a_scheduled_load(world, "released", "syn-1,2026-11-02,5\nsyn-2,2026-11-02,-7\n", "one")
    held = a_scheduled_load(world, "held", "syn-3,2026-11-03,9\n", "two")
    gone = LoadRepository(world.session).create(feed_id=world.feed, file_ref="raw/never.csv", purpose="go_live_test")
    LoadRepository(world.session).abandon(gone.id, "Synthetic.")
    agent_input = rules_input(world.session, feed_id=world.feed, catalogue=world.catalogue)
    assert [h.file_ref for h in agent_input.load_history] == [held.file_ref, released.file_ref]  # newest first
    assert all(h.label.startswith("load 20") for h in agent_input.load_history)
    without = agent_input.model_copy(update={"load_history": []})
    sql = "SELECT _row FROM curated WHERE \"volume\" < 0"
    base = _checked_backtest(world, without, sql)
    full = _checked_backtest(world, agent_input, sql)
    assert (full.total_rows, full.flagged_rows) == (base.total_rows + 3, base.flagged_rows + 1)
    assert [d.label for d in full.deliveries][-2:] == [h.label for h in agent_input.load_history]
    assert [d.rows for d in full.deliveries][-2:] == [1, 2]


def test_the_back_test_reads_the_file_as_the_engine_would_with_the_approved_transformations(world):
    """Phase 7.8, task 4.3, item 8: a rule on a column that an approved transformation cleans (a text date, a
    placeholder) is back-tested on the cleaned values, as the engine would load them; without the transformations the
    same delivery cannot be curated and is left out."""
    from tests.configured import approved_transformation

    a_scheduled_load(world, "released", "syn-1,31-12-2019,5\nsyn-2,15-01-2020,N/A\n", "text_dates")
    approved_transformation(world.session, world.feed, "trade_date", [{"op": "parse_date", "format": "DD-MM-YYYY"}])
    approved_transformation(world.session, world.feed, "traded_qty",
                            [{"op": "placeholder_to_null", "values": ["N/A"]}])
    agent_input = rules_input(world.session, feed_id=world.feed, catalogue=world.catalogue)
    assert [t.source_field for t in agent_input.transformations] == ["trade_date", "traded_qty"]
    assert "parse_date" not in agent_input.model_dump_json()  # the model is not told: code reads the file
    loads_only = agent_input.model_copy(update={"history": [], "current_file": None})  # 5.3b: no profiled file here
    sql = "SELECT _row FROM curated WHERE \"price_date\" < DATE '2020-01-01'"
    back = _checked_backtest(world, loads_only, sql)
    assert (back.status, back.total_rows, back.flagged_rows) == ("ran", 2, 1)
    assert [d.excluded for d in back.deliveries] == [None]
    bare = _checked_backtest(world, loads_only.model_copy(update={"transformations": []}), sql)
    assert bare.status == "not_back_tested" and "do not cast" in bare.deliveries[0].excluded


def test_a_load_whose_file_left_the_store_is_left_out_of_the_back_test_with_its_reason(world):
    a_scheduled_load(world, "released", "syn-1,2026-11-02,5\n", "present")
    agent_input = rules_input(world.session, feed_id=world.feed, catalogue=world.catalogue)
    gone = agent_input.load_history[0].model_copy(update={"file_ref": "raw/gone.csv"})  # left the store
    back = _checked_backtest(world, agent_input.model_copy(update={"load_history": [gone]}), "SELECT _row FROM curated")
    assert back.deliveries[-1].excluded == "file_missing"


def test_the_back_test_also_runs_on_the_feeds_own_profiled_file_so_a_first_onboarding_has_history(world):
    """Task 5.3b: the feed's latest fetched file is a delivery of the back-test ("current file"), read through the
    current mappings and the approved transformations. A presence rule on a column that is empty, or that a
    transformation nulls, flags the whole file and is returned; a presence rule on a filled column is proposed."""
    from core.repositories.source_fetches import SourceFetchRepository
    from tests.configured import approved_transformation

    fetch = SourceFetchRepository(world.session).latest_fetched(world.feed)
    rows = "".join(f"N/A,2026-11-0{n},\n" for n in range(1, 7))  # the id is a placeholder, the quantity is empty
    world.blobs.put(fetch.blob_ref, (LOADED_HEADER + rows).encode())
    approved_transformation(world.session, world.feed, "instr_id",
                            [{"op": "placeholder_to_null", "values": ["N/A"]}])
    agent_input = rules_input(world.session, feed_id=world.feed, catalogue=world.catalogue)
    assert (agent_input.current_file.label, agent_input.current_file.file_ref) == ("current file", fetch.blob_ref)
    end_read(world.session)
    agent_input = agent_input.model_copy(update={"history": []})  # a first onboarding: no past load, no history
    present = lambda element: f'SELECT _row FROM curated WHERE "{element}" IS NULL'  # noqa: E731
    result = record_rules(
        world.session, agent=agent_of(world, answer(("instrument_id_present", present("instrument_id")),
                                                    ("volume_present", present("volume")),
                                                    ("price_date_present", present("price_date")))),
        agent_input=agent_input,
        backtest=backtest_for(agent_input, connector_factory=world.connectors, blob_store=world.blobs,
                              rule_timeout_seconds=5))
    assert result.outcome == "rules_proposed"
    assert sorted((r.rule_key, r.flagged_rows, r.total_rows) for r in result.returned) == [
        ("instrument_id_present", 6, 6), ("volume_present", 6, 6)]
    (proposal,) = pending(world)
    assert proposal.payload["rule_key"] == "price_date_present"
    assert [(d["label"], d["rows"], d["flagged"]) for d in proposal.payload["backtest"]["deliveries"]] == [
        ("current file", 6, 0)]


def _checked_backtest(world, agent_input, sql):
    from core.domain.rules import ProposedRule, RuleRecommenderOutput

    checked = backtest_for(agent_input, connector_factory=world.connectors, blob_store=world.blobs,
                           rule_timeout_seconds=5)(RuleRecommenderOutput(rules=[ProposedRule(
        rule_key="synthetic_rule", description="Synthetic.", sql=sql, element_keys=["volume"])]))
    return checked.rules[0].backtest


# ---------------------------------------------------------------- rules_proposed / flags_most_history_returned


def test_rules_proposed_are_back_tested_and_wait_for_the_steward(world):
    result = propose(world)
    assert isinstance(result, RulesProposed) and result.outcome == "rules_proposed"
    assert len(result.proposal_ids) == 4
    proposals = {p.payload["rule_key"]: p for p in pending(world)}
    assert sorted(proposals) == ["instrument_id_present", "one_row_per_instrument_id_and_price_date",
                                 "price_date_present", "volume_not_negative"]
    assert all(p.required_approver_roles == ["data_steward", "checker"] and p.status == "pending"
               for p in proposals.values())
    back = proposals["volume_not_negative"].payload["backtest"]
    assert (back["status"], back["total_rows"], back["flagged_rows"], back["share"]) == ("ran", 50, 1, 0.02)
    assert [(d["label"], d["rows"], d["flagged"]) for d in back["deliveries"]] == [
        (f"2026-10-{day}", 10, 1 if day == 28 else 0) for day in (26, 27, 28, 29, 30)] + [("current file", 0, 0)]
    assert back["deliveries"][-1]["excluded"] == "file_missing"  # 5.3b: the test feed's fetched file is not in the store
    assert proposals["instrument_id_present"].payload["backtest"]["flagged_rows"] == 1  # the blank id of 10-29
    assert RuleRepository(world.session).current_for_feed(world.feed) == []  # nothing is a rule yet


def test_a_rule_that_flags_most_history_is_returned_not_proposed(world):
    """Assumption 10: volume_in_whole_lots flags 40 of 50 rows (share 0.8), so it is returned; with it the only
    answer, the outcome is flags_most_history_returned and no proposal exists."""
    lots = 'SELECT _row FROM curated WHERE "volume" % 100 <> 0'
    result = propose(world, answer(("volume_in_whole_lots", lots)))
    assert isinstance(result, FlagsMostHistoryReturned) and result.outcome == "flags_most_history_returned"
    (returned,) = result.returned
    assert (returned.rule_key, returned.share, returned.flagged_rows, returned.total_rows) == (
        "volume_in_whole_lots", 0.8, 40, 50)
    assert pending(world) == []


def test_the_mock_agents_answer_has_four_proposed_and_one_returned(world):
    result = propose(world)
    assert [r.rule_key for r in result.returned] == ["volume_in_whole_lots"]
    assert result.refused == []


def test_a_rule_the_checks_refuse_is_listed_with_its_reason_and_makes_no_proposal(world):
    ok = ("fine_rule", SQL_RULE)
    result = propose(world, answer(ok, ("deletes", "DELETE FROM curated"), ("two", f"{SQL_RULE}; {SQL_RULE}"),
                                   ("no_row", "SELECT 1 AS x"), ("broken", "SELECT _row FROM curated WHERE nope = 1"),
                                   ("peeks", "SELECT _row FROM read_csv('.env')"), ("fine_rule", SQL_RULE)))
    assert isinstance(result, RulesProposed) and len(result.proposal_ids) == 1
    reasons = {r.rule_key: r.reason for r in result.refused}
    assert sorted(reasons) == ["broken", "deletes", "fine_rule", "no_row", "peeks", "two"]
    assert "SELECT" in reasons["deletes"] and "exactly one" in reasons["two"] and "_row" in reasons["no_row"]
    assert "nope" in reasons["broken"] and "Permission" in reasons["peeks"]
    assert reasons["fine_rule"] == "The answer holds this rule key twice."


def test_a_rule_naming_an_element_the_feed_does_not_map_is_refused(world):
    bad = {"rules": [{"rule_key": "x", "description": "d.", "sql": SQL_RULE, "element_keys": ["close_price"]}]}
    (refused,) = propose(world, bad).refused
    assert "close_price" in refused.reason


def test_a_key_that_is_current_pending_or_was_rejected_is_not_proposed_again(world):
    approved_rule(world.session, world.feed, "is_current", sql=SQL_RULE, description="Current.")
    rule_proposal(world.session, world.feed, "is_pending", sql=SQL_RULE)
    rule_proposal(world.session, world.feed, "was_rejected", sql=SQL_RULE, decision="rejected")
    result = propose(world, answer(("is_current", SQL_RULE), ("is_pending", SQL_RULE), ("was_rejected", SQL_RULE),
                                   ("brand_new", SQL_RULE)))
    assert {r.rule_key: r.reason for r in result.refused} == {
        "is_current": "The feed already has a rule or a pending proposal with this key.",
        "is_pending": "The feed already has a rule or a pending proposal with this key.",
        "was_rejected": "A steward or checker rejected a rule with this key; it is not proposed again."}
    assert sorted(p.payload["rule_key"] for p in pending(world)) == ["brand_new", "is_pending"]


def test_a_feed_with_no_history_is_proposed_as_not_back_tested(world):
    agent_input = rules_input(world.session, feed_id=world.feed, catalogue=world.catalogue)
    end_read(world.session)
    agent_input = agent_input.model_copy(update={"history": []})
    result = record_rules(world.session, agent=agent_of(world, answer(("rule_a", SQL_RULE))), agent_input=agent_input,
                          backtest=backtest_for(agent_input, connector_factory=world.connectors,
                                                rule_timeout_seconds=5))
    assert len(result.proposal_ids) == 1
    assert pending(world)[0].payload["backtest"] == {"status": "not_back_tested", "total_rows": 0, "flagged_rows": 0,
                                                     "share": None, "deliveries": []}


class Files:
    """A connector over fixed files; a ref that is not there is a source that does not answer."""

    name = "files"

    def __init__(self, files):
        self.files = files

    def fetch(self, ref):
        if ref not in self.files:
            raise SourceUnavailable(ref)
        return FetchedFile(source_ref=ref, filename=ref.rsplit("/", 1)[-1], content=self.files[ref], content_type=None)

    def close(self):
        pass


def test_a_delivery_that_cannot_be_curated_is_left_out_and_listed(world):
    good = b"instr_id,trade_date,traded_qty\nsyn-1,2026-10-26,5\nsyn-2,2026-10-26,-7\n"
    files = {"h/1.csv": good, "h/2.csv": b"instr_id,trade_date,traded_qty\nsyn-1,2026-10-27,12x\n",
             "h/3.csv": b"instr_id,trade_date\nsyn-1,2026-10-28\n", "h/4.csv": b"", }
    agent_input = rules_input(world.session, feed_id=world.feed, catalogue=world.catalogue)
    end_read(world.session)
    agent_input = agent_input.model_copy(update={"history": ["h/1.csv", "h/2.csv", "h/3.csv", "h/4.csv", "h/5.csv"]})
    result = record_rules(world.session, agent=agent_of(world, answer(("volume_not_negative",
                                                                        'SELECT _row FROM curated WHERE "volume" < 0'))),
                          agent_input=agent_input,
                          backtest=backtest_for(agent_input, connector_factory=lambda name: Files(files),
                                                rule_timeout_seconds=5))
    back = pending(world)[0].payload["backtest"]
    assert (back["total_rows"], back["flagged_rows"], back["share"]) == (2, 1, 0.5)
    by_label = {d["label"]: d for d in back["deliveries"]}
    assert (by_label["1"]["rows"], by_label["1"]["flagged"], by_label["1"]["excluded"]) == (2, 1, None)
    assert "do not cast" in by_label["2"]["excluded"] and "not in the file" in by_label["3"]["excluded"]
    assert by_label["4"]["excluded"] and "5" in by_label and by_label["5"]["excluded"] == "source_unavailable"
    assert result.proposal_ids


def test_a_rule_over_the_time_limit_is_refused_in_the_back_test(world):
    slow = ("slow", "SELECT a._row FROM (SELECT range AS _row FROM range(100000)) a, "
                    "(SELECT range AS b FROM range(100000)) c WHERE a._row + c.b = -1")
    agent_input = rules_input(world.session, feed_id=world.feed, catalogue=world.catalogue)
    end_read(world.session)
    result = record_rules(world.session, agent=agent_of(world, answer(slow)), agent_input=agent_input,
                          backtest=backtest_for(agent_input, connector_factory=world.connectors,
                                                rule_timeout_seconds=0.2))
    assert [r.reason for r in result.refused] == ["stopped after 0.2 s"] and result.proposal_ids == []


def test_the_back_test_runs_with_no_connection_held(world, db_session, monkeypatch):
    """Assumption 9: after_answer runs after the call row is committed, outside any transaction."""
    seen = []
    agent_input = rules_input(world.session, feed_id=world.feed, catalogue=world.catalogue)
    end_read(world.session)
    real = backtest_for(agent_input, connector_factory=world.connectors, rule_timeout_seconds=5)

    def watched(output):
        seen.append(db_session.in_transaction())
        return real(output)

    record_rules(world.session, agent=agent_of(world), agent_input=agent_input, backtest=watched)
    assert seen == [False]


def test_a_rule_proposed_during_the_call_is_a_discard_and_nothing_else(world):
    """Assumption 11, the check under the lock: the same key got a pending proposal while the agent answered."""
    def during_the_call(data):
        rule_proposal(world.session, world.feed, "volume_not_negative", sql=SQL_RULE)
        world.session.commit()
        return answer(("volume_not_negative", SQL_RULE))

    def calls():
        return world.session.query(ProposalLogEntry).filter(ProposalLogEntry.input_ref.like("agent-inputs/%")).count()

    before = calls()
    with pytest.raises(RulesAlreadyProposed):
        propose(world, during_the_call)
    assert calls() == before + 1  # the agent's call row: every answered call has one
    assert world.session.query(ProposalLogDiscardRow).count() == 1
    assert [p.payload["rule_key"] for p in pending(world)] == ["volume_not_negative"]  # only the other writer's


# ---------------------------------------------------------------- the decision: steward, then checker


def a_proposal(world, key="close_ok"):
    return rule_proposal(world.session, world.feed, key, sql=SQL_RULE, description="Volume is small.")


def decide(world, proposal, role, decision="approved", note=None, **kw):
    return decide_rule(world.session, proposal_id=proposal.id, user=dev_user_for(role), decision=decision, note=note,
                       **kw)


def test_the_steward_approves_then_the_checker_and_only_then_is_there_a_rule(world):
    proposal = a_proposal(world)
    notifier = InAppNotifier(world.session)
    first = decide(world, proposal, "data_steward", notifier=notifier, checkers=["dev-checker"])
    assert isinstance(first, RuleAwaitsChecker) and first.proposal_id == proposal.id
    assert RuleRepository(world.session).current_for_feed(world.feed) == []  # not a rule after one approval
    told = world.session.scalars(select(NotificationRow).where(NotificationRow.recipient == "dev-checker")).all()
    assert [n.subject for n in told] == ["Rule waits for the checker: close_ok of syn_exchange_volumes"]
    stored = decide(world, proposal, "checker")
    assert isinstance(stored, RuleStored) and (stored.rule.rule_key, stored.rule.version) == ("close_ok", 1)
    assert stored.rule.proposal_id == proposal.id
    assert [r.rule_key for r in RuleRepository(world.session).current_for_feed(world.feed)] == ["close_ok"]


def test_the_wrong_role_for_the_step_is_refused(world):
    proposal = a_proposal(world)
    with pytest.raises(NotAnApprover):
        decide(world, proposal, "checker")  # step 1 is the steward's
    decide(world, proposal, "data_steward")
    with pytest.raises(NotAnApprover):
        decide(world, proposal, "data_steward")  # step 2 is the checker's


def test_a_rejection_needs_a_note_and_ends_the_proposal(world):
    proposal = a_proposal(world)
    with pytest.raises(NoteNeeded):
        decide(world, proposal, "data_steward", decision="rejected", note="  ")
    rejected = decide(world, proposal, "data_steward", decision="rejected", note="Too strict.")
    assert isinstance(rejected, RuleRejected) and rejected.rule_key == "close_ok"
    assert ProposalRepository(world.session).get(proposal.id).status == "rejected"
    with pytest.raises(ProposalAlreadyDecided):
        decide(world, proposal, "data_steward")
    assert RuleRepository(world.session).current_for_feed(world.feed) == []
    assert RuleRepository(world.session).rejected_keys(world.feed) == {"close_ok"}


def test_the_checker_may_reject_after_the_steward_approved(world):
    proposal = a_proposal(world)
    decide(world, proposal, "data_steward")
    decide(world, proposal, "checker", decision="rejected", note="Overlaps another rule.")
    assert ProposalRepository(world.session).get(proposal.id).status == "rejected"
    assert RuleRepository(world.session).current_for_feed(world.feed) == []


def test_only_a_rule_proposal_is_decided_here(world, make_proposal):
    mapping = make_proposal(kind="mapping", feed_id=world.feed)
    with pytest.raises(WrongProposalKind):
        decide(world, mapping, "data_steward")


def test_nothing_in_the_input_is_the_whole_glossary(world):
    """A pointer for the reader: the agent input as the model gets it is a few hundred characters per element."""
    text = rules_input(world.session, feed_id=world.feed, catalogue=world.catalogue).model_dump_json()
    assert len(json.loads(text)["elements"]) == 3 and len(text) < 6000 and uuid.UUID(json.loads(text)["feed_id"])
