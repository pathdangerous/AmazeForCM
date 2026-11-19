"""UploadSourceRepository (phase 7.8, task 2.4): the source an uploaded file is, one per feed."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from core.domain.upload_source import UploadSourceDraft
from core.repositories.errors import UploadSourceNotFound
from core.repositories.feeds import FeedRepository
from core.repositories.upload_sources import UploadSourceRepository
from ports.connectors.catalogue import SourceEntry


def an_uploaded_feed(db_session, code: str = "upload_a"):
    return FeedRepository(db_session).create_candidate(
        code=code, name=f"Upload {code}", source_code=f"upl_{code}", connector="dropzone",
        source_ref=f"dropzone/{code}")


def fields(feed_code: str = "upload_a", **over):
    values = dict(
        code=f"upl_{feed_code}", name=f"Upload {feed_code}", provider="Uploaded by Blake Backend",
        description="A synthetic upload.", licence="Internal use only", permitted_use="Internal analysis",
        redistribution="None", original_name="a.csv", original_ref="uploads/u/a.csv",
        manifest_ref="raw/x/manifest.json", manifest={"format": "csv", "row_count": 3},
        intake_options={"sheet": None, "header_row": 1}, created_by="dev-backend-team")
    values.update(over)
    return values


def test_create_get_and_list(db_session):
    feed_a, feed_b = an_uploaded_feed(db_session, "upload_a"), an_uploaded_feed(db_session, "upload_b")
    repo = UploadSourceRepository(db_session)
    first = repo.create(feed_id=feed_a.id, **fields("upload_a"))
    second = repo.create(feed_id=feed_b.id, **fields("upload_b"))
    assert first.id and first.created_at and first.feed_id == feed_a.id
    assert repo.get_by_code("upl_upload_a") == first and repo.get_by_feed(feed_b.id) == second
    assert [s.code for s in repo.list()] == ["upl_upload_a", "upl_upload_b"]  # oldest first
    assert first.manifest == {"format": "csv", "row_count": 3}
    assert first.intake_options == {"sheet": None, "header_row": 1}


def test_a_missing_source_is_a_named_error_by_code_and_none_by_feed(db_session):
    repo = UploadSourceRepository(db_session)
    with pytest.raises(UploadSourceNotFound):
        repo.get_by_code("upl_nothing")
    assert repo.get_by_feed(an_uploaded_feed(db_session).id) is None


@pytest.mark.parametrize("bad", [{"code": "syn_a"}, {"code": "upl_A"}, {"code": "upl_"}, {"name": " "},
                                 {"licence": ""}, {"permitted_use": " "}, {"redistribution": ""},
                                 {"description": ""}, {"created_by": ""}, {"original_ref": ""},
                                 {"intake_options": {"sheet": None}}, {"intake_options": {"header_row": 0,
                                                                                          "sheet": None}}])
def test_the_draft_refuses_what_the_table_refuses(bad):
    assert UploadSourceDraft(feed_id=uuid4(), **fields()).code == "upl_upload_a"  # the unchanged fields are fine
    with pytest.raises(ValidationError):
        UploadSourceDraft(feed_id=uuid4(), **fields(**bad))


def test_as_entry_is_a_known_private_uploads_entry_with_the_typed_conditions(db_session):
    feed = an_uploaded_feed(db_session)
    source = UploadSourceRepository(db_session).create(feed_id=feed.id, **fields(licence="Vendor licence"))
    entry = source.as_entry()
    assert isinstance(entry, SourceEntry)
    assert (entry.code, entry.name, entry.provider, entry.description) == (
        "upl_upload_a", "Upload upload_a", "Uploaded by Blake Backend", "A synthetic upload.")
    assert (entry.known, entry.visibility, entry.channel, entry.connector, entry.source_ref) == (
        True, "private", "uploads", "dropzone", "dropzone/upload_a")  # the feed's own source_ref
    assert (entry.elements, entry.other_fields, entry.columns, entry.history) == ([], [], {}, [])
    assert (entry.conditions.licence, entry.conditions.permitted_use, entry.conditions.redistribution,
            entry.conditions.external_approvals) == ("Vendor licence", "Internal analysis", "None", [])


def test_the_daily_jobs_catalogue_loader_includes_the_uploads(db_session):
    """apps/api/jobs.py: the catalogue the job loads at each run is the file catalogue plus the upload sources."""
    from contextlib import nullcontext

    from apps.api.jobs import catalogue_with_uploads
    from ports.config import Settings

    UploadSourceRepository(db_session).create(feed_id=an_uploaded_feed(db_session).id, **fields())
    db_session.commit()
    catalogue = catalogue_with_uploads(Settings(_env_file=None), lambda: nullcontext(db_session))
    assert catalogue.get("upl_upload_a").channel == "uploads" and catalogue.get("syn_exchange_volumes")
    assert "upl_upload_a" not in [e.code for e in catalogue.entries]


def test_entries_are_every_upload_as_a_catalogue_entry(db_session):
    repo = UploadSourceRepository(db_session)
    assert repo.entries() == []
    repo.create(feed_id=an_uploaded_feed(db_session, "upload_a").id, **fields("upload_a"))
    repo.create(feed_id=an_uploaded_feed(db_session, "upload_b").id, **fields("upload_b"))
    assert [e.code for e in repo.entries()] == ["upl_upload_a", "upl_upload_b"]
