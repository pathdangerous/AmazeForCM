"""Step 00, direct file intake (phase 7.8, task 2.4; plan assumptions 16 to 20).

inspect_upload() stores the original and reads it with no database; confirm_upload() writes the canonical CSV and
the manifest as the feed's fetched file and creates the feed, its fetch, its upload source and the notice. The
feed is then an ordinary candidate feed: step 02 profiles it unchanged.
"""

from __future__ import annotations

import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import openpyxl
import pytest
from sqlalchemy import text

from core.repositories.errors import DuplicateFeedCode, UploadNotFound
from core.repositories.feeds import FeedRepository
from core.repositories.source_fetches import SourceFetchRepository
from core.repositories.upload_sources import UploadSourceRepository
from core.steps.s00_intake import (
    Inspected,
    InvalidUploadInput,
    NeedsImageExtractionResult,
    NotATableResult,
    TooLarge,
    UnsupportedFormat,
    Uploaded,
    confirm_upload,
    inspect_upload,
    reinspect_upload,
)
from core.steps.s02_profile import Profiled
from ports.config import Settings
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue, SourceConditions
from ports.notify.inapp import InAppNotifier
from ports.storage.local import LocalBlobStore
from tests.conftest import dev_user_for
from tests.step_chains import profile_feed
from tests.test_intake_pdf import HEADER, image_pdf, make_pdf, prose_page, two_page_pdf

DEMO = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic" / "Synthetic_Security_Master.xlsx"
OPERATOR = dev_user_for("backend_team")
TEAM = ["dev-backend-team", "dev-operator"]
BIG = 10**8


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blob")


def book(build) -> bytes:
    wb = openpyxl.Workbook()
    build(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def two_sheets(wb):
    a = wb.active
    a.title = "First"
    a.append(["id", "name"])
    a.append([1, "Ann"])
    b = wb.create_sheet("Second")
    b.append(["code", "label"])
    b.append(["x", "y"])
    b.append(["z", "w"])


def csv_bytes(rows: int = 25) -> bytes:
    return ("id,name,qty\n" + "".join(f"{i},name {i},{i * 2}\n" for i in range(rows))).encode()


def inspect(blobs, data: bytes, filename: str, **kw):
    return inspect_upload(data, filename=filename, blob_store=blobs, max_bytes=BIG, **kw)


def confirm(db_session, blobs, upload_id, *, filename: str, feed_code: str = "upload_a", **kw):
    args = dict(upload_id=upload_id, filename=filename, feed_code=feed_code, feed_name="Upload A", sheet=None,
                header_row=None, blob_store=blobs, user=OPERATOR, notifier=InAppNotifier(db_session),
                backend_team=TEAM)
    args.update(kw)
    return confirm_upload(db_session, **args)


# ---------------------------------------------------------------- inspect: each outcome


def test_a_csv_is_stored_and_inspected(blobs):
    data = csv_bytes(25)
    result = inspect(blobs, data, "Prices Jan.csv")
    assert isinstance(result, Inspected) and result.outcome == "inspected"
    assert (result.format, result.sheets, result.sheet, result.header_row) == ("csv", [], None, 1)
    assert (result.encoding, result.delimiter, result.columns, result.row_count) == (
        "utf-8", ",", ["id", "name", "qty"], 25)
    assert len(result.preview) == 20 and result.preview[0] == ["0", "name 0", "0"]
    assert result.skipped == {} and result.filename == "Prices Jan.csv"
    assert blobs.get(f"uploads/{result.upload_id}/Prices_Jan.csv") == data  # the name made safe for a blob key


@pytest.mark.parametrize(("name", "data", "delimiter"), [
    ("notes.txt", b"a,b\n1,2\n", ","),
    ("tabs.tsv", b"a\tb\n1\t2\n", "\t"),
    ("semi.CSV", b"a;b\n1;2\n", ";"),  # the extension is read in lower case
])
def test_txt_and_tsv_are_read_as_delimited_text(blobs, name, data, delimiter):
    result = inspect(blobs, data, name)
    assert isinstance(result, Inspected) and (result.format, result.delimiter) == ("csv", delimiter)
    assert result.columns == ["a", "b"] and result.row_count == 1


def test_an_xlsx_lists_its_sheets_and_the_operator_picks_one(blobs):
    data = book(two_sheets)
    first = inspect(blobs, data, "book.xlsx")
    assert isinstance(first, Inspected) and (first.format, first.sheets, first.sheet) == (
        "xlsx", ["First", "Second"], "First")
    assert first.columns == ["id", "name"] and first.row_count == 1 and first.encoding is None
    second = inspect(blobs, data, "book.xlsx", sheet="Second")
    assert second.sheet == "Second" and second.columns == ["code", "label"] and second.row_count == 2
    assert second.upload_id != first.upload_id  # every upload call stores its own original


def test_the_stored_original_is_inspected_again_with_other_options(blobs):
    first = inspect(blobs, book(two_sheets), "book.xlsx")
    again = reinspect_upload(first.upload_id, filename="book.xlsx", blob_store=blobs, sheet="Second",
                             header_row=None)
    assert isinstance(again, Inspected) and again.upload_id == first.upload_id and again.sheet == "Second"
    assert blobs.list_keys(f"uploads/{first.upload_id}/") == [f"uploads/{first.upload_id}/book.xlsx"]  # no second copy
    with pytest.raises(UploadNotFound):
        reinspect_upload(uuid4(), filename="book.xlsx", blob_store=blobs, sheet=None, header_row=None)


def test_a_text_pdf_table_is_inspected(blobs):
    result = inspect(blobs, two_page_pdf(), "report.pdf")
    assert isinstance(result, Inspected) and result.format == "pdf"
    assert result.columns == HEADER and result.row_count == 4 and result.preview[0] == ["1", "Ann", "5"]


def test_an_image_only_pdf_needs_image_extraction_and_is_kept(blobs):
    result = inspect(blobs, image_pdf(2), "scan.pdf")
    assert isinstance(result, NeedsImageExtractionResult) and result.outcome == "needs_image_extraction"
    assert result.pages == [1, 2]
    assert blobs.exists(f"uploads/{result.upload_id}/scan.pdf")


@pytest.mark.parametrize(("name", "data"), [
    ("prose.pdf", make_pdf([prose_page(["This synthetic page holds only running prose.", "It has no table."])])),
    ("empty.csv", b""),
    ("broken.xlsx", b"this is not a workbook"),
    ("binary.csv", b"a,b\n1,\x002\n"),
])
def test_a_file_with_no_table_is_not_a_table(blobs, name, data):
    result = inspect(blobs, data, name)
    assert isinstance(result, NotATableResult) and result.outcome == "not_a_table" and result.detail


def test_a_file_over_the_limit_is_too_large_and_nothing_is_stored(blobs):
    result = inspect_upload(csv_bytes(), filename="big.csv", blob_store=blobs, max_bytes=10)
    assert isinstance(result, TooLarge) and result.outcome == "too_large"
    assert (result.size_bytes, result.max_bytes) == (len(csv_bytes()), 10)
    assert blobs.list_keys("uploads/") == []


@pytest.mark.parametrize("name", ["old.xls", "doc.docx", "noextension", "a.csv.exe"])
def test_an_unsupported_format_names_the_formats_taken_and_stores_nothing(blobs, name):
    result = inspect(blobs, b"x", name)
    assert isinstance(result, UnsupportedFormat) and result.outcome == "unsupported_format"
    for taken in (".csv", ".txt", ".tsv", ".xlsx", ".pdf"):
        assert taken in result.detail
    assert blobs.list_keys("uploads/") == []


def test_a_path_in_the_file_name_is_dropped_from_the_blob_key(blobs):
    result = inspect(blobs, csv_bytes(2), "..\\..\\evil dir/..hidden name.csv")
    assert isinstance(result, Inspected)
    assert blobs.list_keys(f"uploads/{result.upload_id}/") == [f"uploads/{result.upload_id}/hidden_name.csv"]


# ---------------------------------------------------------------- confirm


def test_confirm_creates_the_feed_its_fetch_its_upload_source_and_the_notice(db_session, blobs):
    data = csv_bytes(25)
    upload = inspect(blobs, data, "Prices Jan.csv")
    db_session.commit()
    result = confirm(db_session, blobs, upload.upload_id, filename="Prices Jan.csv", description="Synthetic prices")
    assert isinstance(result, Uploaded) and result.outcome == "uploaded"
    assert (result.feed_code, result.row_count, result.columns) == ("upload_a", 25, ["id", "name", "qty"])

    feed = FeedRepository(db_session).get(result.feed_id)
    assert (feed.code, feed.name, feed.stage, feed.connector) == ("upload_a", "Upload A", "candidate", "dropzone")
    assert (feed.source_code, feed.source_ref, feed.requirement_id) == ("upl_upload_a", "dropzone/upload_a", None)

    [fetch] = SourceFetchRepository(db_session).list_for_feed(feed.id)
    day = datetime.now(UTC).date().isoformat()
    assert fetch.id == result.fetch_id and fetch.outcome == "fetched"
    assert fetch.blob_ref == f"raw/upload_a/{day}/{fetch.id}/Prices_Jan.csv"
    canonical = blobs.get(fetch.blob_ref)
    assert canonical.startswith(b"id,name,qty\n0,name 0,0\n") and fetch.size_bytes == len(canonical)
    manifest = json.loads(blobs.get(f"raw/upload_a/{day}/{fetch.id}/manifest.json"))
    assert (manifest["format"], manifest["row_count"], manifest["header_row"]) == ("csv", 25, 1)

    source = UploadSourceRepository(db_session).get_by_feed(feed.id)
    assert (source.code, source.name, source.provider) == ("upl_upload_a", "Upload A", "Uploaded by Blake Backend")
    assert (source.licence, source.permitted_use, source.redistribution) == (
        "Internal use only", "Internal analysis", "None")
    assert source.description == "Synthetic prices" and source.created_by == OPERATOR.id
    assert (source.original_name, source.original_ref) == ("Prices Jan.csv", f"uploads/{upload.upload_id}/Prices_Jan.csv")
    assert source.manifest_ref == f"raw/upload_a/{day}/{fetch.id}/manifest.json"
    assert source.manifest["row_count"] == 25 and source.intake_options == {"sheet": None, "header_row": 1}

    notes = db_session.execute(text("SELECT recipient, subject FROM notifications ORDER BY recipient")).all()
    assert notes == [("dev-backend-team", "File ready to profile: upload_a"),
                     ("dev-operator", "File ready to profile: upload_a")]


def test_confirm_copies_the_original_into_the_drop_folder_when_given_one(db_session, blobs, tmp_path):
    """Phase 7.8, task 4.4: the first file is also the first file of the drop folder, as the operator uploaded it."""
    data = csv_bytes(3)
    upload = inspect(blobs, data, "Prices Jan.csv")
    db_session.commit()
    confirm(db_session, blobs, upload.upload_id, filename="Prices Jan.csv", dropzone_root=tmp_path / "dz")
    [copy] = list((tmp_path / "dz" / "upload_a").iterdir())
    assert copy.name.endswith("_Prices_Jan.csv") and copy.read_bytes() == data


def test_to_canonical_reads_by_extension_with_the_options_or_says_not_a_table():
    from core.steps.s00_intake import to_canonical

    canonical, manifest = to_canonical(b"a;b\r\n1;2\r\n", "x.CSV", {"sheet": None, "header_row": 1})
    assert canonical == b"a,b\n1,2\n" and manifest.format == "csv"
    workbook = book(two_sheets)
    canonical, _ = to_canonical(workbook, "x.xlsx", {"sheet": "Second", "header_row": 1})
    assert canonical.startswith(b"code,label\n")
    assert to_canonical(workbook, "x.xlsx", {})[0].startswith(b"id,name\n")  # no options: the reader's own choice
    for data, name in ((workbook, "x.xlsx"), (b"junk", "x.xlsx"), (b"a,b\n1,2\n", "x.exe"), (image_pdf(1), "x.pdf")):
        result = to_canonical(data, name, {"sheet": "Nope", "header_row": 1} if data is workbook else {})
        assert isinstance(result, NotATableResult) and result.upload_id is None and result.detail


def test_the_operator_types_the_conditions_and_the_options_are_kept(db_session, blobs):
    upload = inspect(blobs, book(two_sheets), "book.xlsx")
    db_session.commit()
    result = confirm(db_session, blobs, upload.upload_id, filename="book.xlsx", sheet="Second", header_row=1,
                     conditions=SourceConditions(licence="Vendor A licence", permitted_use="Research",
                                                 redistribution="Internal"))
    assert isinstance(result, Uploaded) and result.row_count == 2 and result.columns == ["code", "label"]
    source = UploadSourceRepository(db_session).get_by_feed(result.feed_id)
    assert (source.licence, source.permitted_use, source.redistribution) == ("Vendor A licence", "Research", "Internal")
    assert source.intake_options == {"sheet": "Second", "header_row": 1}
    assert source.description  # blank description gets a default text, never blank


def test_confirm_with_options_that_find_no_table_is_not_a_table_and_writes_nothing(db_session, blobs):
    upload = inspect(blobs, book(two_sheets), "book.xlsx")
    db_session.commit()
    result = confirm(db_session, blobs, upload.upload_id, filename="book.xlsx", sheet="Nope")
    assert isinstance(result, NotATableResult) and "Nope" in result.detail
    assert FeedRepository(db_session).list() == []
    assert blobs.list_keys("raw/") == []


def test_a_taken_feed_code_is_a_named_conflict_and_the_same_upload_twice_is_one(db_session, blobs):
    upload = inspect(blobs, csv_bytes(), "a.csv")
    db_session.commit()
    confirm(db_session, blobs, upload.upload_id, filename="a.csv")
    db_session.commit()
    with pytest.raises(DuplicateFeedCode):
        confirm(db_session, blobs, upload.upload_id, filename="a.csv")
    other = inspect(blobs, csv_bytes(), "b.csv")
    db_session.commit()
    with pytest.raises(DuplicateFeedCode):
        confirm(db_session, blobs, other.upload_id, filename="b.csv")  # same code, another upload
    assert len(FeedRepository(db_session).list()) == 1


@pytest.mark.parametrize("code", ["Upload", "1abc", "has space", "a-b", "", "UPPER"])
def test_a_bad_feed_code_is_refused(db_session, blobs, code):
    upload = inspect(blobs, csv_bytes(), "a.csv")
    db_session.commit()
    with pytest.raises(InvalidUploadInput):
        confirm(db_session, blobs, upload.upload_id, filename="a.csv", feed_code=code)
    assert FeedRepository(db_session).list() == []


def test_an_unknown_upload_is_not_found(db_session, blobs):
    with pytest.raises(UploadNotFound):
        confirm(db_session, blobs, uuid4(), filename="a.csv")


def test_confirm_of_an_unsupported_name_is_refused(db_session, blobs):
    upload = inspect(blobs, csv_bytes(), "a.csv")
    db_session.commit()
    with pytest.raises(InvalidUploadInput):
        confirm(db_session, blobs, upload.upload_id, filename="a.xls")


def test_confirm_does_not_touch_the_session_before_the_file_is_read(db_session, blobs, monkeypatch):
    """The file is read and parsed first, with no connection held (CLAUDE.md, the pattern of step 02)."""
    upload = inspect(blobs, csv_bytes(), "a.csv")
    db_session.commit()
    seen = []
    real_get = blobs.get
    monkeypatch.setattr(blobs, "get", lambda key: (seen.append(db_session.in_transaction()), real_get(key))[1])
    confirm(db_session, blobs, upload.upload_id, filename="a.csv")
    assert seen == [False]


# ---------------------------------------------------------------- the feed profiles through step 02 unchanged


def catalogue_for(db_session) -> SourceCatalogue:
    return SourceCatalogue.load(DEFAULT_CATALOGUE).with_uploads(UploadSourceRepository(db_session).entries())


def test_the_demo_file_is_confirmed_and_profiles_through_step_02_unchanged(db_session, blobs):
    upload = inspect(blobs, DEMO.read_bytes(), DEMO.name)
    assert isinstance(upload, Inspected) and (upload.sheet, upload.row_count, len(upload.columns)) == (
        "SYN_Security_Master", 70, 30)
    db_session.commit()
    uploaded = confirm(db_session, blobs, upload.upload_id, filename=DEMO.name, feed_code="demo_security_master")
    assert isinstance(uploaded, Uploaded) and (uploaded.row_count, len(uploaded.columns)) == (70, 30)
    db_session.commit()
    result = profile_feed(db_session, feed_id=uploaded.feed_id, catalogue=catalogue_for(db_session),
                          blob_store=blobs, profiled_by="dev-backend-team")
    assert isinstance(result, Profiled)
    assert (result.profile.row_count, len(result.profile.columns)) == (70, 30)
    assert result.profile.spec.source_code == "upl_demo_security_master" and result.profile.coverage == []


def test_one_hundred_thousand_rows_are_inspected_confirmed_and_profiled_in_time(db_session, blobs):
    header = ",".join(["id"] + [f"col_{i}" for i in range(1, 10)])
    body = "".join(",".join([str(n)] + [f"v{n % 97}_{c}" for c in range(1, 10)]) + "\n" for n in range(100_000))
    data = (header + "\n" + body).encode()
    started = time.perf_counter()
    upload = inspect(blobs, data, "big.csv")
    assert isinstance(upload, Inspected) and upload.row_count == 100_000 and len(upload.columns) == 10
    db_session.commit()
    uploaded = confirm(db_session, blobs, upload.upload_id, filename="big.csv", feed_code="upload_big")
    assert isinstance(uploaded, Uploaded) and uploaded.row_count == 100_000
    db_session.commit()
    result = profile_feed(db_session, feed_id=uploaded.feed_id, catalogue=catalogue_for(db_session),
                          blob_store=blobs, profiled_by="dev-backend-team")
    elapsed = time.perf_counter() - started
    print(f"\n100,000 rows x 10 columns: inspect + confirm + profile in {elapsed:.2f} s")
    assert isinstance(result, Profiled) and result.profile.row_count == 100_000 and len(result.profile.columns) == 10
    assert elapsed < 20


# ---------------------------------------------------------------- settings


def test_upload_max_mb_defaults_to_50_and_is_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("UPLOAD_MAX_MB", raising=False)
    assert Settings(_env_file=None).upload_max_mb == 50
    monkeypatch.setenv("UPLOAD_MAX_MB", "3")
    assert Settings(_env_file=None).upload_max_mb == 3
    monkeypatch.setenv("UPLOAD_MAX_MB", "0")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


# ---------------------------------------------------------------- scanned PDFs: extract, then confirm (task 2.3, D4)


def extractor(tmp_path, provider=None):
    from agents.table_extractor import TableExtractorAgent
    from ports.llm.mock import MockProvider

    return TableExtractorAgent(provider=provider or MockProvider(), blob_store=LocalBlobStore(tmp_path / "agent-blob"))


def extract(db_session, blobs, agent, upload_id, filename="scan.pdf"):
    from core.steps.s00_intake import extract_table

    db_session.commit()  # the model call is made with the session in no transaction
    return extract_table(db_session, upload_id=upload_id, filename=filename, agent=agent, blob_store=blobs)


def unknown_scan_pdf() -> bytes:
    """An image PDF the mock has no recorded table for."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, "PDF")
    return buf.getvalue()


def test_extract_runs_the_agent_on_the_image_pages_and_proposes_a_table(db_session, blobs, tmp_path):
    from core.repositories.proposals import ProposalRepository
    from core.steps.s00_intake import TableExtracted

    upload = inspect(blobs, image_pdf(2), "scan.pdf")
    result = extract(db_session, blobs, extractor(tmp_path), upload.upload_id)
    assert isinstance(result, TableExtracted) and result.outcome == "table_extracted"
    assert (result.header, result.row_count, result.confidence) == (["id", "name", "qty"], 3, 0.9)
    assert result.rows == [["1", "Ann", "5"], ["2", "Bob", "6"], ["3", "Cy", None]] and result.notes
    proposal = ProposalRepository(db_session).get(result.proposal_id)
    assert (proposal.kind, proposal.status, proposal.feed_id) == ("extracted_table", "pending", None)
    assert proposal.payload["upload_id"] == str(upload.upload_id) and proposal.payload["pages"] == [1, 2]
    # rule 4: the model call is logged, and nothing else is created yet
    assert db_session.execute(text("SELECT agent_name, prompt_id FROM proposal_log")).all() == [
        ("TableExtractorAgent", "table_extractor")]
    assert db_session.execute(text("SELECT count(*) FROM feeds")).scalar_one() == 0


def test_extract_shows_only_the_first_twenty_rows_but_counts_all(db_session, blobs, tmp_path):
    from core.domain.extraction import ExtractedTable
    from ports.llm.base import LLMResult

    class ManyRows:
        name = "mock"

        def complete(self, *, prompt_id, prompt_version, messages, response_schema, temperature=0.0):
            table = ExtractedTable(header=["n"], rows=[[str(i)] for i in range(30)], confidence=0.5, notes=[])
            return LLMResult(output=table, provider="mock", model="mock", model_version="1", prompt_id=prompt_id,
                             prompt_version=prompt_version, latency_ms=0, raw_ref=None)

    upload = inspect(blobs, image_pdf(1), "scan.pdf")
    result = extract(db_session, blobs, extractor(tmp_path, ManyRows()), upload.upload_id)
    assert result.row_count == 30 and len(result.rows) == 20 and result.rows[19] == ["19"]


def test_extract_of_a_file_that_is_not_an_image_pdf_is_not_a_table_and_calls_nothing(db_session, blobs, tmp_path):
    upload = inspect(blobs, csv_bytes(), "a.csv")
    result = extract(db_session, blobs, extractor(tmp_path), upload.upload_id, filename="a.csv")
    assert isinstance(result, NotATableResult) and "image" in result.detail
    text_pdf = inspect(blobs, two_page_pdf(), "report.pdf")
    again = extract(db_session, blobs, extractor(tmp_path), text_pdf.upload_id, filename="report.pdf")
    assert isinstance(again, NotATableResult)
    assert db_session.execute(text("SELECT count(*) FROM proposal_log")).scalar_one() == 0


def test_extract_refuses_more_than_ten_pages_and_an_unknown_upload(db_session, blobs, tmp_path):
    upload = inspect(blobs, image_pdf(11), "big.pdf")
    result = extract(db_session, blobs, extractor(tmp_path), upload.upload_id, filename="big.pdf")
    assert isinstance(result, NotATableResult) and "10" in result.detail
    with pytest.raises(UploadNotFound):
        extract(db_session, blobs, extractor(tmp_path), uuid4())
    assert db_session.execute(text("SELECT count(*) FROM proposal_log")).scalar_one() == 0


def test_confirm_creates_the_feed_from_the_extracted_rows_and_the_operator_approves_the_proposal(db_session, blobs,
                                                                                                tmp_path):
    from core.repositories.proposals import ProposalRepository

    upload = inspect(blobs, image_pdf(2), "scan.pdf")
    extracted = extract(db_session, blobs, extractor(tmp_path), upload.upload_id)
    db_session.commit()
    result = confirm(db_session, blobs, upload.upload_id, filename="scan.pdf",
                     extracted_proposal_id=extracted.proposal_id)
    assert isinstance(result, Uploaded) and (result.columns, result.row_count) == (["id", "name", "qty"], 3)

    [fetch] = SourceFetchRepository(db_session).list_for_feed(result.feed_id)
    assert blobs.get(fetch.blob_ref) == b"id,name,qty\n1,Ann,5\n2,Bob,6\n3,Cy,\n"
    day = datetime.now(UTC).date().isoformat()
    manifest = json.loads(blobs.get(f"raw/upload_a/{day}/{fetch.id}/manifest.json"))
    assert (manifest["format"], manifest["extraction"], manifest["pages"], manifest["row_count"]) == (
        "pdf", "image_pdf", [1, 2], 3)
    assert str(extracted.proposal_id) in manifest["reader"]
    source = UploadSourceRepository(db_session).get_by_feed(result.feed_id)
    assert source.original_name == "scan.pdf" and source.manifest["extraction"] == "image_pdf"

    repo = ProposalRepository(db_session)
    assert repo.get(extracted.proposal_id).status == "approved"
    [approval] = repo.approvals(extracted.proposal_id)
    assert (approval.decided_by, approval.decision, approval.role) == (OPERATOR.id, "approved", "backend_team")
    audit = repo.audit(extracted.proposal_id)  # rule 4 in one place: the logged call, the proposal, the decision
    assert audit["agent_name"] == "TableExtractorAgent"


def test_confirm_of_an_image_pdf_without_the_extraction_names_it_and_writes_nothing(db_session, blobs):
    upload = inspect(blobs, image_pdf(1), "scan.pdf")
    db_session.commit()
    result = confirm(db_session, blobs, upload.upload_id, filename="scan.pdf")
    assert isinstance(result, NotATableResult) and "extract" in result.detail
    assert db_session.execute(text("SELECT count(*) FROM feeds")).scalar_one() == 0


def test_confirm_with_a_table_the_model_could_not_read_is_not_a_table_and_the_proposal_stays_pending(
        db_session, blobs, tmp_path):
    from core.repositories.proposals import ProposalRepository

    upload = inspect(blobs, unknown_scan_pdf(), "scan.pdf")
    extracted = extract(db_session, blobs, extractor(tmp_path), upload.upload_id)
    assert extracted.confidence == 0.0 and extracted.header == [] and extracted.rows == []
    db_session.commit()
    result = confirm(db_session, blobs, upload.upload_id, filename="scan.pdf",
                     extracted_proposal_id=extracted.proposal_id)
    assert isinstance(result, NotATableResult)
    assert ProposalRepository(db_session).get(extracted.proposal_id).status == "pending"
    assert db_session.execute(text("SELECT count(*) FROM feeds")).scalar_one() == 0


def test_confirm_refuses_a_proposal_of_another_upload_an_unknown_one_and_one_for_a_readable_file(db_session, blobs,
                                                                                                tmp_path):
    from core.repositories.errors import ProposalNotFound

    scan = inspect(blobs, image_pdf(1), "scan.pdf")
    other = inspect(blobs, image_pdf(1), "other.pdf")
    extracted = extract(db_session, blobs, extractor(tmp_path), other.upload_id, filename="other.pdf")
    db_session.commit()
    with pytest.raises(InvalidUploadInput):  # the proposal reads another upload
        confirm(db_session, blobs, scan.upload_id, filename="scan.pdf", extracted_proposal_id=extracted.proposal_id)
    with pytest.raises(ProposalNotFound):
        confirm(db_session, blobs, scan.upload_id, filename="scan.pdf", extracted_proposal_id=uuid4())
    readable = inspect(blobs, csv_bytes(), "a.csv")
    with pytest.raises(InvalidUploadInput):  # a readable file is not read by the model
        confirm(db_session, blobs, readable.upload_id, filename="a.csv", extracted_proposal_id=extracted.proposal_id)
    assert db_session.execute(text("SELECT count(*) FROM feeds")).scalar_one() == 0


def test_a_proposal_is_confirmed_once_and_confirm_reads_the_file_before_the_session(db_session, blobs, tmp_path,
                                                                                   monkeypatch):
    from core.repositories.errors import ProposalAlreadyDecided

    upload = inspect(blobs, image_pdf(1), "scan.pdf")
    extracted = extract(db_session, blobs, extractor(tmp_path), upload.upload_id)
    db_session.commit()
    seen = []
    real_get = blobs.get
    monkeypatch.setattr(blobs, "get", lambda key: (seen.append(db_session.in_transaction()), real_get(key))[1])
    confirm(db_session, blobs, upload.upload_id, filename="scan.pdf", extracted_proposal_id=extracted.proposal_id)
    assert seen and not any(seen)
    with pytest.raises(ProposalAlreadyDecided):
        confirm(db_session, blobs, upload.upload_id, filename="scan.pdf", feed_code="upload_b",
                extracted_proposal_id=extracted.proposal_id)
