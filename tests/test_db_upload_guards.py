"""Migration 0021's upload_sources table and its guards (phase 7.8, task 2.4), by raw SQL.

Append-only (UPDATE, DELETE and TRUNCATE refused), the code format and the non-blank texts, one source per feed,
and the match guard: the feed's source_code equals the row's code and its connector is dropzone. The connector
CHECK on feeds accepts dropzone. Against 0020 every test fails.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from core.repositories.feeds import FeedRepository
from tests.test_db_onboarding_guards import _branch_off, answered_by, refused

INSERT = (
    "INSERT INTO upload_sources (code, feed_id, name, provider, description, licence, permitted_use, redistribution, "
    "original_name, original_ref, manifest_ref, manifest, intake_options, created_by) "
    "VALUES (:code, :feed, :name, :provider, :description, :licence, :permitted_use, :redistribution, "
    ":original_name, :original_ref, :manifest_ref, CAST(:manifest AS jsonb), CAST(:intake_options AS jsonb), "
    ":created_by) RETURNING id"
)
OPTIONS = '{"sheet": null, "header_row": 1}'
TEXTS = ["name", "provider", "description", "licence", "permitted_use", "redistribution", "original_name",
         "original_ref", "manifest_ref", "created_by"]


def an_uploaded_feed(s, code="upload_a"):
    return FeedRepository(s).create_candidate(code=code, name=code, source_code=f"upl_{code}", connector="dropzone",
                                              source_ref=f"dropzone/{code}").id


def params(feed, **over):
    values = {"code": "upl_upload_a", "feed": feed, "name": "Upload A", "provider": "Uploaded by Blake",
              "description": "Synthetic.", "licence": "Internal use only", "permitted_use": "Internal analysis",
              "redistribution": "None", "original_name": "a.csv", "original_ref": "uploads/u/a.csv",
              "manifest_ref": "raw/x/manifest.json", "manifest": "{}", "intake_options": OPTIONS,
              "created_by": "dev-backend-team"}
    values.update(over)
    return values


def test_a_dropzone_feed_and_its_upload_source_are_accepted(db_session):
    feed = an_uploaded_feed(db_session)
    row = db_session.execute(text(INSERT), params(feed)).scalar_one()
    assert db_session.scalar(text("SELECT feed_id FROM upload_sources WHERE id = :i"), {"i": row}) == feed
    assert db_session.scalar(text("SELECT connector FROM feeds WHERE id = :f"), {"f": feed}) == "dropzone"


def test_the_feeds_connector_check_still_refuses_other_connectors(db_session):
    with pytest.raises(IntegrityError, match="ck_feeds_connector"):
        with db_session.begin_nested():
            db_session.execute(text("INSERT INTO feeds (code, name, source_code, connector, source_ref) "
                                    "VALUES ('f', 'F', 'upl_f', 'ftp', 'x')"))


@pytest.mark.parametrize("sql", [
    "UPDATE upload_sources SET name = name WHERE id = :i",
    "DELETE FROM upload_sources WHERE id = :i",
    "TRUNCATE upload_sources CASCADE",
], ids=["update", "delete", "truncate"])
def test_upload_sources_is_append_only(db_session, sql):
    row = db_session.execute(text(INSERT), params(an_uploaded_feed(db_session))).scalar_one()
    with pytest.raises(DBAPIError, match="upload_sources is append-only"):
        with db_session.begin_nested():
            db_session.execute(text(sql), {"i": row})


@pytest.mark.parametrize("code", ["syn_upload_a", "upl_Upload", "upl_", "upl_1a", "upl a", "UPL_a"])
def test_the_code_format(db_session, code):
    """The feed reads exactly this code, so the match guard passes and only the format check can refuse."""
    feed = db_session.execute(text("INSERT INTO feeds (code, name, source_code, connector, source_ref) "
                                   "VALUES ('odd_feed', 'F', :code, 'dropzone', 'dropzone/odd_feed') RETURNING id"),
                              {"code": code}).scalar_one()
    refused(db_session, "ck_upload_sources_code_format", INSERT, **params(feed, code=code))


@pytest.mark.parametrize("column", TEXTS)
def test_a_text_is_not_blank(db_session, column):
    feed = an_uploaded_feed(db_session)
    refused(db_session, f"ck_upload_sources_{column}", INSERT, **params(feed, **{column: "  "}))


@pytest.mark.parametrize("bad", ['[]', '{"sheet": null}', '{"sheet": null, "header_row": 0}',
                                 '{"sheet": 3, "header_row": 1}', '{"sheet": null, "header_row": "1"}', 'null'])
def test_the_intake_options_are_an_object_with_a_sheet_and_a_header_row(db_session, bad):
    feed = an_uploaded_feed(db_session)
    refused(db_session, "ck_upload_sources_intake_options", INSERT, **params(feed, intake_options=bad))


def test_the_manifest_is_an_object(db_session):
    feed = an_uploaded_feed(db_session)
    refused(db_session, "ck_upload_sources_manifest", INSERT, **params(feed, manifest="[]"))


def test_one_source_per_feed_and_per_code(db_session):
    """Both are unique; the match guard makes them the same fact (the code is the feed's source_code)."""
    feed = an_uploaded_feed(db_session)
    db_session.execute(text(INSERT), params(feed))
    refused(db_session, "uq_upload_sources_(code|feed_id)", INSERT, **params(feed))


GUARD = "upload_sources: feed % does not read source % through the dropzone"


def test_the_feed_must_read_the_rows_code(db_session):
    feed = an_uploaded_feed(db_session)
    answered_by(db_session, "does not read source", INSERT, params(feed, code="upl_another"),
                _branch_off("amaze_guard_upload_source", GUARD))


def test_the_feed_must_use_the_dropzone_connector(db_session):
    feed = FeedRepository(db_session).create_candidate(
        code="file_feed", name="F", source_code="upl_file_feed", connector="local_file",
        source_ref="files/f.csv").id
    answered_by(db_session, "does not read source", INSERT, params(feed, code="upl_file_feed"),
                _branch_off("amaze_guard_upload_source", GUARD))


def test_a_feed_that_does_not_exist_is_refused(db_session):
    answered_by(db_session, "does not read source", INSERT, params(uuid.uuid4()),
                _branch_off("amaze_guard_upload_source", GUARD))


def test_a_feed_without_a_source_is_refused(db_session):
    feed = FeedRepository(db_session).create_candidate(code="plain_feed", name="Plain").id
    answered_by(db_session, "does not read source", INSERT, params(feed),
                _branch_off("amaze_guard_upload_source", GUARD))
