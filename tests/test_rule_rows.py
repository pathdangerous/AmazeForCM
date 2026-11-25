"""Phase 7.20 T1: click a rule, see its rows (plan docs/plans/phase-7.20.md). Read only: no model call, no write.

A pending rule proposal runs its SQL in the sandbox on the feed's latest file through the draft configuration (source
`draft`); an approved rule reads the flags of the feed's newest load that has records (source `load`, no SQL runs).
Each answer is a typed result: rows_shown, rule_not_found, no_file, sql_refused.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from core.domain.load import STAGES, LoadStageDraft, StandardRecordDraft
from core.engine.sandbox import Sandbox
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.repositories.standard_records import StandardRecordRepository
from core.repositories.configuration import RuleRepository
from core.steps.rule_rows import NoFile, RuleNotFound, RowsShown, SqlRefused, draft_rows, load_rows, rule_rows_plan
from core.steps.s20_create_product import FinishContext
from ports.config import Settings
from ports.notify.inapp import InAppNotifier
from ports.storage.local import LocalBlobStore
from tests.conftest import running_app
from tests.configured import VOLUME_RULE, loadable_feed, logged_call, mapping_proposal, volume_csv
from tests.test_commit_visibility import committed, own_schema  # noqa: F401 (own_schema is a fixture)
from tests.test_exception_rerun import rerun, volume_template, waived
from tests.test_exceptions_view import deliver

pytestmark = pytest.mark.anyio
OVER = "SELECT _row FROM curated WHERE volume > 100000"
HEADERS = {"X-Dev-User": "dev-data-steward"}


@pytest.fixture
def world(db_session, glossary, tmp_path):
    feed_id, live = loadable_feed(db_session, "syn_rule_rows")
    context = FinishContext(notifier=InAppNotifier(db_session), threshold=Decimal("90"), operations=[], backend_team=[])
    return SimpleNamespace(session=db_session, feed_id=feed_id, feed=feed_id, live=live, context=context,
                           blobs=LocalBlobStore(tmp_path / "blob"), n=0)  # feed, live, context: what deliver() reads


def fetch(world, content: str) -> None:
    world.n += 1
    key = f"raw/syn_rule_rows/{world.n}/f.csv"
    world.blobs.put(key, content.encode())
    SourceFetchRepository(world.session).record(feed_id=world.feed_id, outcome="fetched", blob_ref=key,
                                                size_bytes=len(content))


def pending_rule(world, key: str, sql: str, element_keys: list[str]):
    return ProposalRepository(world.session).create(
        proposal_log_id=logged_call(world.session, "RuleRecommenderAgent", "rule_recommender").id, kind="rule",
        payload={"rule_key": key, "sql": sql, "description": f"{key} description", "element_keys": element_keys},
        autonomy="A2", required_approver_roles=["data_steward", "checker"], feed_id=world.feed_id)


def draft(world, proposal, **query):
    plan = rule_rows_plan(world.session, proposal.id)
    return draft_rows(plan, blob_store=world.blobs, rule_timeout_seconds=5, **{"failing_only": False, "page": 1,
                                                                                "size": 50, **query})


def a_loaded_load(world, records: list[StandardRecordDraft]):
    """A go-live test load of the feed with these standard records, loaded the way the database allows."""
    repo = LoadRepository(world.session)
    load = repo.create(feed_id=world.feed_id, file_ref=f"local://raw/{world.feed_id}/f.csv", purpose="go_live_test")
    StandardRecordRepository(world.session).add_many(load_id=load.id, feed_id=world.feed_id, records=records)
    now = datetime.now(UTC)
    repo.record_stages(load.id, [LoadStageDraft(stage=name, outcome="stage_completed", rows_in=len(records),
                                                rows_out=len(records), detail={}, started_at=now, finished_at=now)
                                 for name in STAGES])
    return repo.finish(load.id, status="loaded")


def record(row: int, volume: int, flags=(), waived=()) -> StandardRecordDraft:
    return StandardRecordDraft(row_number=row, values={"instrument_id": f"syn-{row}", "volume": volume},
                               flags=list(flags), waived=list(waived))


# ---------------------------------------------------------------- source (a): a rule proposal runs on the latest file


def test_a_draft_rule_marks_exactly_the_rows_the_sandbox_returns(world, tmp_path):
    content = volume_csv(10, 3)
    fetch(world, content)
    proposal = pending_rule(world, "volume_draft", OVER, ["volume"])
    shown = draft(world, proposal)
    assert isinstance(shown, RowsShown)
    path = tmp_path / "check.csv"  # the same file, read the back-test's way, gives the same rows
    path.write_text(content)
    box = Sandbox(timeout_seconds=5)
    box.load_csv(path)
    box.execute("CREATE TABLE curated AS SELECT _row, CAST(traded_qty AS BIGINT) AS volume FROM raw")
    box.lock()
    expected = box.run_rule(OVER)
    box.close()
    assert [r.row for r in shown.rows if not r.passes] == expected == [1, 2, 3]
    assert (shown.counts.rows, shown.counts.failing, shown.counts.waived, shown.counts.shown) == (10, 3, 0, 10)
    assert (shown.source, shown.load_id) == ("draft", None)
    assert (shown.rule.rule_key, shown.rule.sql, shown.rule.element_keys) == ("volume_draft", OVER, ["volume"])
    assert shown.rows[0].values == {"instrument_id": "syn-0001", "price_date": "2026-11-02", "volume": 200000}
    assert not any(r.waived for r in shown.rows)


def test_the_rules_columns_come_first_and_every_one_of_them_is_affected(world):
    fetch(world, "instr_id,trade_date,traded_qty\nsyn-1,2026-11-02,200000\nsyn-2,,1000\nsyn-3,2026-11-02,1000\n")
    proposal = pending_rule(world, "volume_or_date", "SELECT _row FROM curated WHERE volume > 100000 OR price_date IS NULL",
                            ["volume", "price_date"])
    shown = draft(world, proposal)
    assert [(c.key, c.affected) for c in shown.columns] == [("volume", True), ("price_date", True),
                                                            ("instrument_id", False)]
    assert [r.row for r in shown.rows if not r.passes] == [1, 2]


def test_failing_only_and_paging_cut_the_rows_not_the_counts(world):
    fetch(world, volume_csv(10, 4))
    proposal = pending_rule(world, "volume_draft", OVER, ["volume"])
    only = draft(world, proposal, failing_only=True, size=3)
    assert [r.row for r in only.rows] == [1, 2, 3]
    assert (only.counts.rows, only.counts.failing, only.counts.shown, only.page, only.size) == (10, 4, 4, 1, 3)
    second = draft(world, proposal, failing_only=True, size=3, page=2)
    assert [r.row for r in second.rows] == [4]
    all_rows = draft(world, proposal, size=4, page=3)
    assert [r.row for r in all_rows.rows] == [9, 10]
    assert [r.row for r in draft(world, proposal, page=9).rows] == []  # past the end: empty, not an error


def test_a_feed_with_no_fetched_file_is_no_file(world):
    plan = rule_rows_plan(world.session, pending_rule(world, "volume_draft", OVER, ["volume"]).id)
    shown = draft_rows(replace(plan, file_ref=None), blob_store=world.blobs, rule_timeout_seconds=5, failing_only=False,
                       page=1, size=50)
    assert isinstance(shown, NoFile) and "no fetched file" in shown.reason


def test_a_fetched_file_that_is_no_longer_stored_is_no_file(world):
    shown = draft(world, pending_rule(world, "volume_draft", OVER, ["volume"]))  # the fixture's fetch has no blob
    assert isinstance(shown, NoFile) and "no longer stored" in shown.reason


def test_a_file_without_a_mapped_column_is_no_file(world):
    fetch(world, "instr_id,trade_date\nsyn-1,2026-11-02\n")
    shown = draft(world, pending_rule(world, "volume_draft", OVER, ["volume"]))
    assert isinstance(shown, NoFile) and "traded_qty" in shown.reason


def test_a_value_that_does_not_cast_is_no_file_as_in_the_back_test(world):
    fetch(world, "instr_id,trade_date,traded_qty\nsyn-1,2026-11-02,many\n")
    shown = draft(world, pending_rule(world, "volume_draft", OVER, ["volume"]))
    assert isinstance(shown, NoFile) and "do not cast" in shown.reason


@pytest.mark.parametrize("sql, why", [
    ("DELETE FROM curated", "SELECT"),
    ("SELECT volume FROM curated", "_row"),
    ("SELECT _row FROM curated WHERE nonsense > 1", "nonsense"),
])
def test_sql_the_sandbox_refuses_is_sql_refused_with_its_reason(world, sql, why):
    fetch(world, volume_csv(3, 1))
    shown = draft(world, pending_rule(world, "volume_draft", sql, ["volume"]))
    assert isinstance(shown, SqlRefused) and why in shown.reason


# ---------------------------------------------------------------- source (b): an approved rule reads the load's flags


def test_an_approved_rule_marks_the_rows_whose_flags_hold_its_key_only(world):
    [rule] = RuleRepository(world.session).current_for_feed(world.feed_id)
    load = a_loaded_load(world, [record(1, 200000, [VOLUME_RULE]), record(2, 1000), record(3, 1000, ["other_rule"]),
                                 record(4, 300000, [VOLUME_RULE, "other_rule"])])
    for ident in (rule.id, rule.proposal_id):  # a rules row id, or the id of the proposal it came from
        plan = rule_rows_plan(world.session, ident)
        shown = load_rows(world.session, plan, failing_only=False, page=1, size=50)
        assert isinstance(shown, RowsShown) and (shown.source, shown.load_id) == ("load", load.id)
        assert [(r.row, r.passes, r.waived) for r in shown.rows] == [
            (1, False, False), (2, True, False), (3, True, False), (4, False, False)]
        assert (shown.counts.rows, shown.counts.failing, shown.counts.waived) == (4, 2, 0)
        assert shown.rows[0].values == {"instrument_id": "syn-1", "volume": 200000}
    only = load_rows(world.session, plan, failing_only=True, page=1, size=1)
    assert [r.row for r in only.rows] == [1] and only.counts.shown == 2
    assert [r.row for r in load_rows(world.session, plan, failing_only=True, page=2, size=1).rows] == [4]


def test_the_rows_of_a_real_held_load_are_marked_by_the_engines_flags(world):
    volume_template(world)
    held = deliver(world, volume_csv(10, 2))
    [rule] = RuleRepository(world.session).current_for_feed(world.feed_id)
    shown = load_rows(world.session, rule_rows_plan(world.session, rule.id), failing_only=False, page=1, size=50)
    assert shown.load_id == held.id and [r.row for r in shown.rows if not r.passes] == [1, 2]
    assert (shown.counts.rows, shown.counts.failing, shown.counts.waived) == (10, 2, 0)


def test_a_waived_row_is_its_own_state_and_is_not_counted_as_failing(world):
    volume_template(world)
    held = deliver(world, volume_csv(10, 2))
    waived(world, held.id, VOLUME_RULE)
    rerun_load = rerun(world, held)[1][1].run.load  # the rerun's records carry the waiver
    [rule] = RuleRepository(world.session).current_for_feed(world.feed_id)
    plan = rule_rows_plan(world.session, rule.id)
    shown = load_rows(world.session, plan, failing_only=False, page=1, size=50)
    assert shown.load_id == rerun_load.id
    assert [(r.row, r.passes, r.waived) for r in shown.rows[:3]] == [(1, False, True), (2, False, True), (3, True, False)]
    assert (shown.counts.rows, shown.counts.failing, shown.counts.waived) == (10, 0, 2)
    assert load_rows(world.session, plan, failing_only=True, page=1, size=50).rows == []


def test_an_approved_rule_with_no_load_is_no_file(world):
    [rule] = RuleRepository(world.session).current_for_feed(world.feed_id)
    plan = rule_rows_plan(world.session, rule.id)
    assert isinstance(load_rows(world.session, plan, failing_only=False, page=1, size=50), NoFile)


def test_an_id_that_is_no_rule_is_rule_not_found(world):
    mapping = mapping_proposal(world.session, world.feed_id, "other_col", ["volume"])
    assert isinstance(rule_rows_plan(world.session, uuid4()), RuleNotFound)
    assert isinstance(rule_rows_plan(world.session, mapping.id), RuleNotFound)  # a proposal of another kind


# ---------------------------------------------------------------- the endpoint


@pytest.fixture
async def client(db_session, migrated_engine, world):
    from apps.api.deps import get_blob_store_port
    from apps.api.main import app

    async with running_app(engine=migrated_engine, session=db_session, settings=Settings(_env_file=None)) as c:
        app.dependency_overrides[get_blob_store_port] = lambda: world.blobs
        yield c


async def test_the_endpoint_answers_each_outcome_for_any_backend_role_and_caps_the_size(world, client):
    world.session.commit()  # the API's read() ends the transaction: what the test wrote is a savepoint release only
    url = "/api/amaze/rules/{}/rows"
    assert (await client.get(url.format(uuid4()), headers={"X-Dev-User": "dev-requester"})).status_code == 403
    assert (await client.get(url.format(uuid4()), headers=HEADERS)).json() == {"outcome": "rule_not_found"}
    proposal = pending_rule(world, "volume_draft", OVER, ["volume"])
    world.session.commit()
    nothing = await client.get(url.format(proposal.id), headers=HEADERS)
    assert nothing.status_code == 200 and nothing.json()["outcome"] == "no_file"
    fetch(world, volume_csv(10, 2))
    world.session.commit()
    before = world.session.scalar(text("SELECT count(*) FROM proposal_log"))
    big = await client.get(url.format(proposal.id) + "?failing_only=true&page=1&size=100000", headers=HEADERS)
    body = big.json()
    assert big.status_code == 200 and body["outcome"] == "rows_shown", big.text
    assert body["size"] == Settings(_env_file=None).rule_rows_max_size  # cut to the setting
    assert [r["row"] for r in body["rows"]] == [1, 2] and body["counts"]["failing"] == 2
    assert body["rows"][0]["passes"] is False
    assert world.session.scalar(text("SELECT count(*) FROM proposal_log")) == before  # no model call, nothing logged


async def test_no_connection_is_held_while_the_rule_runs_in_the_sandbox(own_schema, tmp_path):
    """The sandbox and the file read run with the pool empty (rule 2's pattern, like a model call)."""
    from apps.api.deps import get_blob_store_port
    from apps.api.main import app
    from core.engine import sandbox as sandbox_module
    from ports.db import get_sessionmaker

    blobs = LocalBlobStore(tmp_path / "blob")
    key = "raw/syn_rule_rows/1/f.csv"
    blobs.put(key, volume_csv(6, 2).encode())
    with get_sessionmaker(own_schema)() as session:
        feed_id, _ = loadable_feed(session, "syn_rule_rows")
        SourceFetchRepository(session).record(feed_id=feed_id, outcome="fetched", blob_ref=key, size_bytes=10)
        proposal = ProposalRepository(session).create(
            proposal_log_id=logged_call(session, "RuleRecommenderAgent", "rule_recommender").id, kind="rule",
            payload={"rule_key": "volume_draft", "sql": OVER, "description": "d", "element_keys": ["volume"]},
            autonomy="A2", required_approver_roles=["data_steward", "checker"], feed_id=feed_id)
        session.commit()
        proposal_id = proposal.id

    seen = {"get": [], "run_rule": []}
    real_get, real_run = LocalBlobStore.get, sandbox_module.Sandbox.run_rule

    def watched_get(self, ref):
        seen["get"].append(own_schema.pool.checkedout())
        return real_get(self, ref)

    def watched_run(self, sql):
        seen["run_rule"].append(own_schema.pool.checkedout())
        return real_run(self, sql)

    LocalBlobStore.get, sandbox_module.Sandbox.run_rule = watched_get, watched_run
    try:
        async with running_app(engine=own_schema, settings=Settings(_env_file=None)) as client:
            app.dependency_overrides[get_blob_store_port] = lambda: blobs
            response = await client.get(f"/api/amaze/rules/{proposal_id}/rows", headers=HEADERS)
    finally:
        LocalBlobStore.get, sandbox_module.Sandbox.run_rule = real_get, real_run
    assert response.json()["counts"]["failing"] == 2, response.text
    assert seen["get"] and seen["run_rule"]
    assert set(seen["get"]) == {0} and set(seen["run_rule"]) == {0}
