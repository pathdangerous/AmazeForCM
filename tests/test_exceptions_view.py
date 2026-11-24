"""Phase 7.14 T1: the exceptions view (plan docs/plans/phase-7.14.md, section 2.1). Read only.

An exception is one flagged row and one rule key in its flags; a group is a rule key and a value pattern within one
load. The pattern is the shape of the values of the elements the rule names (digit runs 9, upper-case runs A,
lower-case runs a); one definition, the database function amaze_value_pattern() (migration 0033), and a Python twin
in core/engine/pattern.py that must give the same answer.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text

from core.engine.pattern import EMPTY, ROW, value_pattern
from core.repositories.transformations import KeyPolicyRepository
from core.steps.exceptions import feed_exceptions, group_key, load_exceptions, rule_elements
from core.steps.s20_create_product import FinishContext
from ports.config import Settings
from ports.notify.inapp import InAppNotifier
from ports.storage.local import LocalBlobStore
from tests.conftest import running_app
from tests.configured import VOLUME_RULE, approved_rule, go_live, loadable_feed, volume_csv
from tests.step_chains import run_scheduled

ID_RULE = "instrument_id_is_internal"
ID_SQL = "SELECT _row FROM curated WHERE instrument_id NOT LIKE 'int-%'"
HEADER = "instr_id,trade_date,traded_qty\n"


@pytest.fixture
def world(db_session, glossary, tmp_path):
    feed, live = loadable_feed(db_session, "syn_exceptions")
    blobs = LocalBlobStore(tmp_path / "blob")
    context = FinishContext(notifier=InAppNotifier(db_session), threshold=Decimal("90"), operations=[],
                            backend_team=[])
    return SimpleNamespace(feed=feed, live=live, session=db_session, blobs=blobs, context=context, n=0)


def deliver(world, content: str):
    world.n += 1
    key = f"raw/syn_exceptions/{world.n}/f.csv"
    world.blobs.put(key, content.encode())
    return run_scheduled(world.session, world.blobs, world.feed, world.live.id, file_ref=key,
                         context=world.context).run.load


def with_id_rule(world):
    approved_rule(world.session, world.feed, ID_RULE, sql=ID_SQL, description="Instrument ids are internal.")
    world.live = go_live(world.session, world.feed)


# ---------------------------------------------------------------- the pattern


@pytest.mark.parametrize("values, keys, expected", [
    ({"v": "-12.50"}, ["v"], "-9.9"),
    ({"v": "-3.1"}, ["v"], "-9.9"),
    ({"v": "ABC-123"}, ["v"], "A-9"),
    ({"v": "syn-0001"}, ["v"], "a-9"),
    ({"v": "Ab c"}, ["v"], "Aa a"),
    ({"v": 200000}, ["v"], "9"),
    ({"v": 1.5}, ["v"], "9.9"),
    ({"v": True}, ["v"], "a"),
    ({"v": None}, ["v"], EMPTY),
    ({"v": "   "}, ["v"], EMPTY),
    ({}, ["v"], EMPTY),
    ({"b": "X1", "a": "y"}, ["b", "a"], "a | A9"),  # in key order, whatever the order given
    ({"a": "x"}, [], ROW),
])
def test_the_pattern_is_the_shape_of_the_values_and_python_agrees_with_the_database(db_session, values, keys, expected):
    import json

    assert value_pattern(values, keys) == expected
    in_sql = db_session.scalar(text("SELECT amaze_value_pattern(CAST(:v AS jsonb), CAST(:k AS text[]))"),
                               {"v": json.dumps(values), "k": "{" + ",".join(keys) + "}"})
    assert in_sql == expected


def test_a_rule_names_the_mapped_elements_its_sql_names_as_whole_words():
    elements = ["instrument_id", "volume", "price_date", "vol"]
    assert rule_elements("SELECT _row FROM curated WHERE volume > 100000", elements) == ["volume"]
    assert rule_elements("SELECT _row FROM curated WHERE price_date IS NULL OR Volume < 0", elements) == [
        "price_date", "volume"]
    assert rule_elements("SELECT _row FROM curated WHERE 1 = 1", elements) == []


def test_the_group_key_is_stable_and_short():
    assert group_key(VOLUME_RULE, "9") == group_key(VOLUME_RULE, "9")
    assert group_key(VOLUME_RULE, "9") != group_key(VOLUME_RULE, "-9")
    assert len(group_key(VOLUME_RULE, "9")) == 16


# ---------------------------------------------------------------- one load


def test_a_held_load_shows_its_groups_counts_and_sample_rows(world):
    load = deliver(world, volume_csv(10, 2))  # 80.0: held
    view = load_exceptions(world.session, load.id)
    assert (view.status, view.can_fix, view.rows, view.flagged_rows, view.exceptions) == ("held", True, 10, 2, 2)
    [group] = view.groups
    assert (group.rule_key, group.elements, group.pattern, group.exceptions, group.state) == (
        VOLUME_RULE, ["volume"], "9", 2, "open")
    assert group.description == "Volume is at most 100000."
    assert group.group_key == group_key(VOLUME_RULE, "9")
    assert [s.row_number for s in group.samples] == [1, 2]
    assert group.samples[0].values["volume"] in ("200000", 200000)


def test_a_row_flagged_by_two_rules_is_two_exceptions_and_patterns_split_groups(world):
    with_id_rule(world)
    csv = HEADER + "syn-1,2026-11-02,200000\nSYN-2,2026-11-02,5\nint-3,2026-11-02,5\nint-4,2026-11-02,5\n"
    load = deliver(world, csv)  # row 1: both rules; row 2: the id rule, another pattern; 50.0, held
    view = load_exceptions(world.session, load.id)
    assert (view.flagged_rows, view.exceptions) == (2, 3)
    got = {(g.rule_key, g.pattern): g.exceptions for g in view.groups}
    assert got == {(VOLUME_RULE, "9"): 1, (ID_RULE, "a-9"): 1, (ID_RULE, "A-9"): 1}


def test_samples_stop_at_five_and_groups_come_largest_first_with_paging(world):
    with_id_rule(world)
    rows = [f"syn-{n},2026-11-02,{200000 if n <= 8 else 5}" for n in range(1, 11)]  # 8 volume, 10 id (a-9)
    rows += ["ABC-1,2026-11-02,5", "ABC-2,2026-11-02,5", "Q1,2026-11-02,5"]  # id rule: A-9 twice, A9 once
    load = deliver(world, HEADER + "\n".join(rows) + "\n")
    view = load_exceptions(world.session, load.id)
    assert [(g.rule_key, g.pattern, g.exceptions) for g in view.groups] == [
        (ID_RULE, "a-9", 10), (VOLUME_RULE, "9", 8), (ID_RULE, "A-9", 2), (ID_RULE, "A9", 1)]
    assert [len(g.samples) for g in view.groups] == [5, 5, 2, 1]
    page = load_exceptions(world.session, load.id, limit=2, offset=1)
    assert view.total_groups == page.total_groups == 4
    assert [(g.rule_key, g.pattern) for g in page.groups] == [(VOLUME_RULE, "9"), (ID_RULE, "A-9")]
    only = load_exceptions(world.session, load.id, rule=ID_RULE)
    assert {g.rule_key for g in only.groups} == {ID_RULE} and only.total_groups == 3


def test_a_load_with_no_flag_has_no_group(world):
    load = deliver(world, volume_csv(5, 0))
    view = load_exceptions(world.session, load.id)
    assert (view.status, view.can_fix, view.exceptions, view.groups, view.total_groups) == (
        "released", False, 0, [], 0)


def test_a_released_load_with_flags_is_shown_but_takes_no_fix(world):
    load = deliver(world, volume_csv(10, 1))  # 90.0: released
    view = load_exceptions(world.session, load.id)
    assert (view.status, view.can_fix, len(view.groups)) == ("released", False, 1)


def test_duplicate_key_groups_on_the_key_columns_elements(world):
    KeyPolicyRepository(world.session).set(world.feed, "flag", ["instr_id"], "dev-data-steward", "Synthetic.")
    csv = HEADER + "syn-1,2026-11-02,5\nsyn-1,2026-11-03,5\nsyn-2,2026-11-02,5\n"
    load = deliver(world, csv)
    groups = {(g.rule_key, g.pattern): g for g in load_exceptions(world.session, load.id).groups}
    group = groups[("duplicate_key", "a-9")]
    assert group.elements == ["instrument_id"] and group.exceptions == 2


def test_the_view_counts_in_the_database_whatever_the_number_of_rows(world):
    small, large = deliver(world, volume_csv(10, 2)), deliver(world, volume_csv(5000, 1000))
    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    engine = world.session.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        load_exceptions(world.session, small.id)
        few = len(statements)
        statements.clear()
        view = load_exceptions(world.session, large.id)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(statements) == few  # the same queries for 10 rows and 5 000: groups are counted in SQL
    assert view.groups[0].exceptions == 1000 and len(view.groups[0].samples) == 5


def test_a_feed_lists_its_loads_with_their_counts(world):
    held, released = deliver(world, volume_csv(10, 3)), deliver(world, volume_csv(10, 0))
    rows = {r.load_id: r for r in feed_exceptions(world.session, world.feed)}
    assert (rows[held.id].status, rows[held.id].flagged_rows, rows[held.id].exceptions, rows[held.id].rows) == (
        "held", 3, 3, 10)
    assert (rows[released.id].flagged_rows, rows[released.id].exceptions) == (0, 0)


# ---------------------------------------------------------------- the API (read only)

OPERATOR = {"X-Dev-User": "dev-operator"}


@pytest.fixture
async def client(db_session, migrated_engine):
    async with running_app(engine=migrated_engine, session=db_session, settings=Settings(_env_file=None)) as c:
        yield c


@pytest.mark.anyio
async def test_the_api_answers_the_load_and_the_feed_views(world, client):
    load = deliver(world, volume_csv(10, 2))
    response = await client.get(f"/api/amaze/loads/{load.id}/exceptions?limit=10", headers=OPERATOR)
    assert response.status_code == 200
    body = response.json()
    assert body["can_fix"] is True and body["groups"][0]["pattern"] == "9" and body["limit"] == 10
    feed = await client.get(f"/api/amaze/feeds/{world.feed}/exceptions", headers=OPERATOR)
    assert feed.status_code == 200 and feed.json()[0]["load_id"] == str(load.id)


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/amaze/loads/{id}/exceptions", "/api/amaze/feeds/{id}/exceptions"])
async def test_an_unknown_load_or_feed_is_404_and_the_view_is_backend_only(client, path):
    import uuid

    url = path.format(id=uuid.uuid4())
    assert (await client.get(url, headers=OPERATOR)).status_code == 404
    assert (await client.get(url, headers={"X-Dev-User": "dev-requester"})).status_code == 403


@pytest.mark.anyio
async def test_a_page_over_fifty_is_refused(world, client):
    load = deliver(world, volume_csv(10, 2))
    assert (await client.get(f"/api/amaze/loads/{load.id}/exceptions?limit=51", headers=OPERATOR)).status_code == 422
