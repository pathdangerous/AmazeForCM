"""Phase 7.9: amaze_delete_feed() (migration 0027) removes every row of one feed and nothing else.

Plan: docs/plans/phase-7.9.md, sections 3, 4 and 7. The function is tested here at the database level; the step
that wraps it (blobs, notices, requirements) is tested in test_feed_lifecycle.py.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from core.steps.s20_create_product import FinishContext
from ports.notify.inapp import InAppNotifier
from ports.storage.local import LocalBlobStore
from tests.conftest import fresh_schema, replay_ddl
from tests.configured import loadable_feed, volume_csv
from tests.step_chains import run_scheduled
from tests.test_onboarding import approve, current, make_world, run_to, work

# table -> how its rows of feed :f are found. Direct: a feed_id column. Children: through their parent row.
DIRECT = ("feed_go_lives", "feed_key_policies", "feed_profiles", "feed_registrations", "feed_signoffs", "glossary_runs",
          "load_certifications", "loads", "mappings", "onboarding_sessions", "products", "proposals", "rules",
          "source_choices", "source_fetches", "standard_records", "transformations", "upload_sources",
          "feed_status_events")
CHILDREN = {
    "load_stages": "load_id IN (SELECT id FROM loads WHERE feed_id = :f)",
    "product_elements": "product_id IN (SELECT id FROM products WHERE feed_id = :f)",
    "product_grants": "product_id IN (SELECT id FROM products WHERE feed_id = :f)",
    "onboarding_events": "session_id IN (SELECT id FROM onboarding_sessions WHERE feed_id = :f)",
    "onboarding_jobs": "session_id IN (SELECT id FROM onboarding_sessions WHERE feed_id = :f)",
    "approvals": "proposal_id IN (SELECT id FROM proposals WHERE feed_id = :f)",
    "proposal_log": "id IN (SELECT proposal_log_id FROM proposals WHERE feed_id = :f "
                    "UNION SELECT proposal_log_id FROM glossary_runs WHERE feed_id = :f)",
}
TABLES = (*DIRECT, *CHILDREN)
KEPT_WITH_THE_PROPOSALS = ("proposals", "approvals", "proposal_log")  # a proposal a glossary element came from stays


def count_rows(session: Session, table: str, feed_id: UUID) -> int:
    where = f"feed_id = :f" if table in DIRECT else CHILDREN[table]
    return session.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), {"f": feed_id}).scalar_one()


def counts(session: Session, feed_id: UUID) -> dict[str, int]:
    return {t: count_rows(session, t, feed_id) for t in TABLES}


def delete_feed(session: Session, feed_id: UUID, by: str = "dev-operator", reason: str = "Synthetic: the demo is over."):
    return session.execute(text("SELECT amaze_delete_feed(:f, :b, :r)"), {"f": feed_id, "b": by, "r": reason}).scalar_one()


def onboarded(db_session, tmp_path, code: str = "syn_security_master"):
    """The security master, uploaded and onboarded to a released first load and a product (one operator)."""
    w = make_world(db_session, tmp_path)
    run_to(w, "go_live")
    approve(w)
    work(w)
    assert current(w).status == "done"
    return w


def another_feed(db_session, tmp_path):
    """A second registered feed with one released load, records and certification: it must not change."""
    feed, live = loadable_feed(db_session)
    blobs = LocalBlobStore(tmp_path / "other-blob")
    blobs.put("raw/syn_loadable/1/f.csv", volume_csv(10, 1).encode())
    context = FinishContext(notifier=InAppNotifier(db_session), threshold=Decimal("90"), operations=[], backend_team=[])
    run_scheduled(db_session, blobs, feed, live.id, file_ref="raw/syn_loadable/1/f.csv", context=context)
    return feed


def test_every_table_with_a_feed_id_is_known_to_the_delete_tests(db_session):
    """A new table that names a feed must be added to DIRECT, or this fails: the delete would miss it."""
    named = set(db_session.execute(text(
        "SELECT table_name FROM information_schema.columns WHERE column_name = 'feed_id' "
        "AND table_schema = current_schema()")).scalars())
    assert named - {"feed_deletions"} == set(DIRECT), named ^ set(DIRECT)


def test_deleting_a_feed_removes_its_rows_and_nothing_of_another_feed(db_session, glossary, tmp_path):
    w = onboarded(db_session, tmp_path)
    other = another_feed(db_session, tmp_path)
    before, other_before = counts(db_session, w.feed_id), counts(db_session, other)
    assert all(before[t] > 0 for t in ("loads", "standard_records", "load_certifications", "feed_go_lives", "mappings",
                                       "rules", "products", "feed_profiles", "feed_signoffs", "feed_registrations",
                                       "onboarding_sessions", "onboarding_events", "glossary_runs", "upload_sources",
                                       "source_fetches", "load_stages", "approvals", "proposals", "proposal_log")), before
    elements_before = db_session.execute(text("SELECT count(*) FROM glossary_elements")).scalar_one()
    kept = set(db_session.execute(text(
        "SELECT p.id FROM proposals p WHERE p.feed_id = :f AND p.id IN (SELECT proposal_id FROM glossary_elements "
        "WHERE proposal_id IS NOT NULL)"), {"f": w.feed_id}).scalars())

    deleted = delete_feed(db_session, w.feed_id)

    after = counts(db_session, w.feed_id)
    for table in TABLES:
        if table in KEPT_WITH_THE_PROPOSALS:
            continue
        assert after[table] == 0, (table, after[table])
    leftover = set(db_session.execute(text("SELECT id FROM proposals WHERE feed_id = :f"), {"f": w.feed_id}).scalars())
    assert leftover == kept  # only the proposals a glossary element was made from stay, naming the deleted feed
    assert db_session.execute(text("SELECT count(*) FROM feeds WHERE id = :f"), {"f": w.feed_id}).scalar_one() == 0
    assert counts(db_session, other) == other_before  # another feed's rows: unchanged
    assert db_session.execute(text("SELECT count(*) FROM glossary_elements")).scalar_one() == elements_before
    assert deleted["feeds"] == 1
    for table in TABLES:
        if table not in KEPT_WITH_THE_PROPOSALS:
            assert deleted.get(table, 0) == before[table], (table, deleted.get(table), before[table])


def test_the_deletion_is_recorded_with_its_counts(db_session, glossary, tmp_path):
    w = onboarded(db_session, tmp_path)
    deleted = delete_feed(db_session, w.feed_id, by="dev-operator", reason="Synthetic: repeat the demo.")
    row = db_session.execute(text("SELECT feed_id, code, name, \"by\", reason, rows_deleted FROM feed_deletions")).one()
    assert (row.feed_id, row.code, row.by, row.reason) == (w.feed_id, "syn_security_master", "dev-operator",
                                                           "Synthetic: repeat the demo.")
    assert row.rows_deleted == deleted and row.rows_deleted["loads"] >= 2 and row.rows_deleted["standard_records"] == 140  # test load + first load


def test_a_deletion_needs_a_reason_and_a_person_and_a_feed_that_exists(db_session, glossary, tmp_path):
    w = onboarded(db_session, tmp_path)
    for by, reason, code in (("dev-operator", "  ", "23514"), ("", "Synthetic.", "23514")):
        with pytest.raises(DBAPIError) as refused:
            with db_session.begin_nested():
                delete_feed(db_session, w.feed_id, by=by, reason=reason)
        assert refused.value.orig.sqlstate == code
    with pytest.raises(DBAPIError) as missing:
        with db_session.begin_nested():
            delete_feed(db_session, UUID(int=9))
    assert missing.value.orig.sqlstate == "P0002"
    assert db_session.execute(text("SELECT count(*) FROM feeds WHERE id = :f"), {"f": w.feed_id}).scalar_one() == 1


def test_a_feed_with_a_load_in_progress_is_not_deleted(db_session, glossary, tmp_path):
    w = onboarded(db_session, tmp_path)
    db_session.execute(text(
        "INSERT INTO loads (feed_id, file_ref, purpose, status, go_live_id) "
        "SELECT :f, 'raw/x/y.csv', 'scheduled', 'received', id FROM feed_go_lives WHERE feed_id = :f LIMIT 1"),
        {"f": w.feed_id})
    with pytest.raises(DBAPIError) as refused:
        with db_session.begin_nested():
            delete_feed(db_session, w.feed_id)
    assert refused.value.orig.sqlstate == "55006"
    assert db_session.execute(text("SELECT count(*) FROM feeds WHERE id = :f"), {"f": w.feed_id}).scalar_one() == 1


def test_a_deleted_feeds_code_and_source_can_be_used_again(db_session, glossary, tmp_path):
    w = onboarded(db_session, tmp_path)
    delete_feed(db_session, w.feed_id)
    assert db_session.execute(text(
        "SELECT count(*) FROM feeds WHERE code = 'syn_security_master' OR source_code = 'syn_security_master'")
    ).scalar_one() == 0


# ---------------------------------------------------------------- acceptance: the demo can be repeated


def delete_through_the_step(w, db_session):
    from core.steps.feed_lifecycle import delete_feed as delete_step
    from ports.identity.dev import DevIdentity

    return delete_step(db_session, feed_id=w.feed_id, user=DevIdentity().get_user("dev-operator"),
                       reason="Synthetic: repeat the demo.", blob_store=w.ctx.blob_store,
                       dropzone_root=w.settings.dropzone_root, notifier=InAppNotifier(db_session))


def test_the_demo_file_is_onboarded_deleted_and_onboarded_again_twice_in_a_row(db_session, glossary, tmp_path):
    """Acceptance: the same file under the same code, every time, to a released first load: no quarantine (no earlier
    load of the same bytes is left), no duplicate_linked (no feed of that source is left), released."""
    from core.repositories.loads import LoadRepository

    seen = []
    for _ in range(2):  # the whole sequence, twice in a row: onboard, delete, onboard again
        first = onboarded(db_session, tmp_path)
        deleted = delete_through_the_step(first, db_session)
        assert deleted.code == "syn_security_master" and deleted.rows_deleted["feeds"] == 1
        again = onboarded(db_session, tmp_path)  # the same file, the same code, the same folders
        loads = LoadRepository(db_session).list_for_feed(again.feed_id)
        scheduled = [x for x in loads if x.purpose == "scheduled"]
        assert [x.status for x in scheduled] == ["released"], [(x.status, x.arrival_outcome) for x in loads]
        assert scheduled[0].arrival_outcome == "on_time_complete"
        assert db_session.execute(text("SELECT count(*) FROM feeds WHERE code = 'syn_security_master'")).scalar_one() == 1
        seen.append(again.feed_id)
        delete_through_the_step(again, db_session)  # leave nothing for the next round
    assert len(set(seen)) == 2  # a new feed each time
    assert db_session.execute(text("SELECT count(*) FROM feed_deletions WHERE code = 'syn_security_master'")
                              ).scalar_one() == 4


# ---------------------------------------------------------------- a plain DELETE is still refused, table by table

# every table with a DELETE guard (append-only, or guarded history) that an onboarded feed fills; loads and products
# have no DELETE guard (an FK and their own checks decide), and feed_status_events and feed_deletions are covered in
# test_feed_lifecycle.py and below
GUARDED_TABLES = ("approvals", "feed_go_lives", "feed_profiles", "feed_registrations", "feed_signoffs",
                  "glossary_elements", "glossary_runs", "load_certifications", "load_stages", "mappings",
                  "onboarding_events", "onboarding_jobs", "onboarding_sessions", "product_elements", "proposal_log",
                  "proposals", "rules", "source_fetches", "standard_records", "transformations", "upload_sources")


@pytest.fixture(scope="module")
def committed_world(test_database_url, offline_ddl, tmp_path_factory):
    """One onboarded feed whose rows really commit, in a schema of its own (the append-only tables refuse DELETE, so
    the schema is dropped whole afterwards): each parametrised case below only tries a DELETE on one table."""
    from core.seed import seed_glossary

    with fresh_schema(test_database_url, "w79") as (engine, url, schema):
        replay_ddl(engine, offline_ddl)
        session = Session(engine)
        try:
            seed_glossary(session)
            session.commit()
            w = onboarded(session, tmp_path_factory.mktemp("w79"))
            session.commit()
            yield SimpleNamespace(engine=engine, feed_id=w.feed_id)
        finally:
            session.close()


@pytest.mark.parametrize("table", GUARDED_TABLES)
def test_a_plain_delete_is_still_refused(committed_world, table):
    with committed_world.engine.connect() as connection:
        assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() > 0, f"{table} is empty here"
        refused = None
        try:
            connection.execute(text(f"DELETE FROM {table}"))
        except DBAPIError as exc:
            refused = exc
        connection.rollback()
    assert refused is not None and refused.orig.sqlstate == "23001", table  # restrict_violation


def test_a_delete_naming_another_feed_in_the_setting_is_refused_too(committed_world):
    with committed_world.engine.connect() as connection:
        connection.execute(text("SELECT set_config('amaze.deleting_feed', :f, true)"), {"f": str(UUID(int=5))})
        refused = None
        try:
            connection.execute(text("DELETE FROM standard_records"))
        except DBAPIError as exc:
            refused = exc
        connection.rollback()
    assert refused is not None and refused.orig.sqlstate == "23001"


def test_the_deletion_record_itself_cannot_be_deleted_or_changed(db_session):
    db_session.execute(text(
        "INSERT INTO feed_deletions (feed_id, code, name, \"by\", reason, rows_deleted) "
        "VALUES (gen_random_uuid(), 'syn_x', 'X', 'dev-operator', 'Synthetic.', '{}'::jsonb)"))
    for statement in ("DELETE FROM feed_deletions", "UPDATE feed_deletions SET reason = 'Changed.'",
                      "TRUNCATE feed_deletions"):
        with pytest.raises(DBAPIError) as refused:
            with db_session.begin_nested():
                db_session.execute(text(statement))
        assert refused.value.orig.sqlstate == "23001", statement
