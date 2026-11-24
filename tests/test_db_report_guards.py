"""Migration 0035's guards (phase 7.15 plan, 2.2 and 2.4), by raw SQL: the report's hash and versions, its narrative
states, its append-only rule, and loads.rerun_by. Each refusal is checked by its message."""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.configured import logged_call
from tests.test_exceptions_view import deliver, world  # noqa: F401 (world is a fixture)

INSERT = ("INSERT INTO reports (kind, subject_id, subject_role, period_start, period_end, version, numbers, "
          "numbers_sha256, narrative, narrative_status, narrative_note, narrative_log_id, generated_by) VALUES "
          "(:kind, :subject, :role, :start, :end, :version, CAST(:numbers AS jsonb), 'overwritten', :narrative, "
          ":status, :note, :log, 'job') RETURNING id")


def params(**over) -> dict:
    return {"kind": "daily", "subject": "dev-data-owner", "role": "data_owner", "start": "2026-11-02",
            "end": "2026-11-03", "version": 1, "numbers": json.dumps({"loads": 3}), "narrative": None,
            "status": "none", "note": None, "log": None, **over}


def refused(session, match: str, **over):
    with pytest.raises(DBAPIError, match=match):
        with session.begin_nested():
            session.execute(text(INSERT), params(**over))


def test_the_database_sets_the_hash_of_the_numbers_itself(db_session):
    row = db_session.execute(text(INSERT), params()).scalar_one()
    stored = db_session.scalar(text("SELECT numbers_sha256 FROM reports WHERE id = :i"), {"i": row})
    assert stored != "overwritten"
    assert stored == db_session.scalar(text("SELECT encode(sha256(convert_to(numbers::text, 'UTF8')), 'hex') "
                                            "FROM reports WHERE id = :i"), {"i": row})
    assert stored == hashlib.sha256(json.dumps({"loads": 3}).encode()).hexdigest()  # jsonb prints {"loads": 3}


def test_only_the_next_version_of_a_report_is_taken(db_session):
    db_session.execute(text(INSERT), params())
    refused(db_session, "the next version of the daily report of dev-data-owner for 2026-11-02 is 2, not 3", version=3)
    refused(db_session, "is 2, not 1", version=1)
    db_session.execute(text(INSERT), params(version=2))
    db_session.execute(text(INSERT), params(start="2026-11-03", end="2026-11-04"))  # another day starts at 1
    db_session.execute(text(INSERT), params(subject="dev-data-steward", role="data_steward"))  # so does another person


def test_a_report_is_append_only(db_session):
    row = db_session.execute(text(INSERT), params()).scalar_one()
    for sql in ("UPDATE reports SET narrative = 'x' WHERE id = :i", "DELETE FROM reports WHERE id = :i"):
        with pytest.raises(DBAPIError, match="append-only"):
            with db_session.begin_nested():
                db_session.execute(text(sql), {"i": row})
    with pytest.raises(DBAPIError, match="append-only|truncate"):
        with db_session.begin_nested():
            db_session.execute(text("TRUNCATE reports"))


@pytest.mark.parametrize("over, match", [
    ({"kind": "weekly"}, "ck_reports_kind"),
    ({"role": "requester"}, "ck_reports_subject_role"),
    ({"subject": " "}, "ck_reports_named"),
    ({"numbers": "[1]"}, "ck_reports_numbers"),
    ({"end": "2026-11-04"}, "ck_reports_period_fits_kind"),  # a day is one day
    ({"kind": "monthly", "start": "2026-11-02", "end": "2026-12-02"}, "ck_reports_period_fits_kind"),
    ({"kind": "monthly", "start": "2026-11-01", "end": "2026-11-30"}, "ck_reports_period_fits_kind"),
    ({"status": "maybe"}, "ck_reports_narrative_status|ck_reports_narrative_fits_status"),
])
def test_a_report_names_its_kind_its_person_and_its_period(db_session, over, match):
    refused(db_session, match, **over)


def test_a_monthly_report_is_one_calendar_month(db_session):
    db_session.execute(text(INSERT), params(kind="monthly", start="2026-11-01", end="2026-12-01"))


def test_the_narrative_states_each_need_what_they_say(world, db_session):
    log = logged_call(world.session, "ReportSummariserAgent", "report_summariser").id
    refused(world.session, "ck_reports_narrative_fits_status", status="written", narrative="A text.", log=None)
    refused(world.session, "ck_reports_narrative_fits_status", status="written", narrative=" ", log=log)
    refused(world.session, "ck_reports_narrative_fits_status", status="written", narrative=None, log=log)
    refused(world.session, "ck_reports_narrative_fits_status", status="refused", note=None, log=log)
    refused(world.session, "ck_reports_narrative_fits_status", status="refused", note="A figure.", log=None)
    refused(world.session, "ck_reports_narrative_fits_status", status="refused", narrative="Kept.", note="x", log=log)
    refused(world.session, "ck_reports_narrative_fits_status", status="none", narrative="A text.")
    refused(world.session, "ck_reports_narrative_fits_status", status="none", log=log)
    world.session.execute(text(INSERT), params(status="written", narrative="A text.", log=log))
    world.session.execute(text(INSERT), params(version=2, status="refused", note="Stated 42, which is no number.", log=log))
    world.session.execute(text(INSERT), params(version=3))


# ---------------------------------------------------------------- loads.rerun_by

RERUN = ("INSERT INTO loads (feed_id, file_ref, purpose, go_live_id, rerun_of_load_id, rerun_reason, rerun_by) "
         "VALUES (:feed, :file, 'scheduled', :live, :of, 'Waived.', :by) RETURNING id")


def test_who_reran_is_set_with_the_rerun_and_never_changes(world):
    held = deliver(world, __import__("tests.configured", fromlist=["volume_csv"]).volume_csv(10, 2))
    ok = {"feed": world.feed, "file": held.file_ref, "live": world.live.id, "of": held.id, "by": "dev-data-steward"}
    with pytest.raises(DBAPIError, match="ck_loads_rerun_by_names_a_person"):
        with world.session.begin_nested():
            world.session.execute(text(RERUN), {**ok, "by": " "})
    rerun = world.session.execute(text(RERUN), ok).scalar_one()
    with pytest.raises(DBAPIError, match="is set once"):
        with world.session.begin_nested():
            world.session.execute(text("UPDATE loads SET rerun_by = 'dev-operator' WHERE id = :i"), {"i": rerun})


def test_a_load_that_is_no_rerun_names_nobody(world):
    with pytest.raises(DBAPIError, match="names who reran it but is no rerun"):
        with world.session.begin_nested():
            world.session.execute(text(
                "INSERT INTO loads (feed_id, file_ref, purpose, go_live_id, rerun_by) "
                "VALUES (:feed, 'raw/x.csv', 'scheduled', :live, 'dev-data-steward')"),
                {"feed": world.feed, "live": world.live.id})
