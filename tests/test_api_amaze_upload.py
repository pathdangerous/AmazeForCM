"""The upload API (phase 7.8, task 2.4): POST /api/amaze/uploads, .../{id}/inspect and .../{id}/confirm.

Backend team only (dev-operator and dev-backend-team hold the role; the other backend roles get 403, the requester
403, no user 401). The body is the raw file (application/octet-stream): no python-multipart. The outcome is in the
JSON body; too_large is 413, unsupported_format and not_a_table are 422, needs_image_extraction is 200.
"""

from __future__ import annotations

import io
import uuid

import openpyxl
import pytest
from sqlalchemy import text

from core.seed import seed_glossary
from ports.config import Settings
from ports.storage.local import LocalBlobStore
from tests.conftest import running_app
from tests.schedules import SCHEDULE
from tests.test_intake_pdf import image_pdf, make_pdf, prose_page, two_page_pdf

pytestmark = pytest.mark.anyio

OPERATOR = {"X-Dev-User": "dev-operator"}
TEAM = {"X-Dev-User": "dev-backend-team"}
STEWARD = {"X-Dev-User": "dev-data-steward"}
REQUESTER = {"X-Dev-User": "dev-requester"}
OCTET = {"Content-Type": "application/octet-stream"}
URL = "/api/amaze/uploads"


def csv_bytes(rows: int = 25) -> bytes:
    return ("id,name,qty\n" + "".join(f"{i},name {i},{i * 2}\n" for i in range(rows))).encode()


def workbook() -> bytes:
    wb = openpyxl.Workbook()
    a = wb.active
    a.title = "First"
    a.append(["id", "name"])
    a.append([1, "Ann"])
    b = wb.create_sheet("Second")
    b.append(["code", "label"])
    b.append(["x", "y"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blob")


@pytest.fixture
def dropzone(tmp_path):
    return tmp_path / "dropzone"


@pytest.fixture
async def client(db_session, migrated_engine, blobs, dropzone):
    from apps.api.deps import get_blob_store_port
    from apps.api.main import app

    seed_glossary(db_session)
    async with running_app(engine=migrated_engine,
                           settings=Settings(_env_file=None, upload_max_mb=1, dropzone_root=dropzone),
                           session=db_session) as c:
        app.dependency_overrides[get_blob_store_port] = lambda: blobs
        yield c


async def upload(client, data: bytes, filename: str = "a.csv", headers=OPERATOR, **params):
    return await client.post(URL, params={"filename": filename, **params}, content=data, headers={**headers, **OCTET})


async def confirm(client, upload_id, headers=OPERATOR, **body):
    payload = {"filename": "a.csv", "feed_code": "upload_a", "feed_name": "Upload A", "sheet": None,
               "header_row": None, "schedule": SCHEDULE, **body}
    return await client.post(f"{URL}/{upload_id}/confirm", json=payload, headers=headers)


# ---------------------------------------------------------------- upload


async def test_a_csv_is_inspected(client, blobs):
    response = await upload(client, csv_bytes(25))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "inspected" and body["format"] == "csv" and body["columns"] == ["id", "name", "qty"]
    assert (body["row_count"], len(body["preview"]), body["header_row"], body["delimiter"]) == (25, 20, 1, ",")
    assert blobs.exists(f"uploads/{body['upload_id']}/a.csv")


async def test_a_workbook_sheet_and_header_row_are_chosen_with_query_parameters(client):
    first = (await upload(client, workbook(), "book.xlsx")).json()
    assert (first["sheets"], first["sheet"]) == (["First", "Second"], "First")
    second = (await upload(client, workbook(), "book.xlsx", sheet="Second", header_row=1)).json()
    assert second["sheet"] == "Second" and second["columns"] == ["code", "label"]


async def test_a_text_pdf_is_inspected_and_an_image_pdf_needs_extraction_with_200(client):
    text_pdf = await upload(client, two_page_pdf(), "report.pdf")
    assert text_pdf.status_code == 200 and text_pdf.json()["format"] == "pdf" and text_pdf.json()["row_count"] == 4
    image = await upload(client, image_pdf(), "scan.pdf")
    assert image.status_code == 200
    assert image.json()["outcome"] == "needs_image_extraction" and image.json()["pages"] == [1]


async def test_a_file_over_the_limit_is_413_by_its_content_length_and_by_its_real_length(client, blobs):
    big = b"a,b\n" + b"1,2\n" * 300_000  # 1.2 MB against UPLOAD_MAX_MB=1
    declared = await upload(client, big)
    assert declared.status_code == 413
    assert declared.json()["outcome"] == "too_large" and declared.json()["max_bytes"] == 1024 * 1024

    async def chunks():  # no Content-Length: the real length is checked
        for _ in range(3):
            yield b"1,2\n" * 100_000

    chunked = await client.post(URL, params={"filename": "a.csv"}, content=chunks(), headers={**OPERATOR, **OCTET})
    assert chunked.status_code == 413 and chunked.json()["outcome"] == "too_large"
    assert blobs.list_keys("uploads/") == []


@pytest.mark.parametrize(("data", "name", "outcome"), [
    (b"x", "old.xls", "unsupported_format"),
    (b"x", "noextension", "unsupported_format"),
    (b"", "empty.csv", "not_a_table"),
    (b"not a workbook", "broken.xlsx", "not_a_table"),
    (make_pdf([prose_page(["Running prose only, with no table at all."])]), "prose.pdf", "not_a_table"),
])
async def test_unsupported_and_not_a_table_are_422_with_the_outcome_in_the_body(client, data, name, outcome):
    response = await upload(client, data, name)
    assert response.status_code == 422
    assert response.json()["outcome"] == outcome and response.json()["detail"]


async def test_the_file_name_is_required(client):
    response = await client.post(URL, content=csv_bytes(), headers={**OPERATOR, **OCTET})
    assert response.status_code == 422


# ---------------------------------------------------------------- inspect again


async def test_the_stored_original_is_inspected_again_with_another_sheet(client):
    stored = (await upload(client, workbook(), "book.xlsx")).json()
    response = await client.post(f"{URL}/{stored['upload_id']}/inspect", headers=OPERATOR,
                                 json={"filename": "book.xlsx", "sheet": "Second", "header_row": None})
    assert response.status_code == 200, response.text
    assert response.json()["upload_id"] == stored["upload_id"] and response.json()["sheet"] == "Second"
    assert response.json()["columns"] == ["code", "label"]


async def test_inspect_again_unknown_upload_is_404_and_a_bad_sheet_is_422(client):
    body = {"filename": "book.xlsx", "sheet": None, "header_row": None}
    assert (await client.post(f"{URL}/{uuid.uuid4()}/inspect", json=body, headers=OPERATOR)).status_code == 404
    stored = (await upload(client, workbook(), "book.xlsx")).json()
    bad = await client.post(f"{URL}/{stored['upload_id']}/inspect", headers=OPERATOR,
                            json={**body, "sheet": "Nope"})
    assert bad.status_code == 422 and bad.json()["outcome"] == "not_a_table" and "Nope" in bad.json()["detail"]
    other = await client.post(f"{URL}/{stored['upload_id']}/inspect", headers=OPERATOR,
                              json={**body, "filename": "book.xls"})
    assert other.status_code == 422 and other.json()["outcome"] == "unsupported_format"


# ---------------------------------------------------------------- confirm


async def test_confirm_creates_a_feed_the_backend_pages_show_and_step_02_profiles(client, db_session):
    stored = (await upload(client, csv_bytes(25))).json()
    response = await confirm(client, stored["upload_id"], description="Synthetic prices")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "uploaded" and body["feed_code"] == "upload_a"
    assert (body["row_count"], body["columns"]) == (25, ["id", "name", "qty"]) and body["fetch_id"] and body["feed_id"]

    feeds = (await client.get("/api/amaze/feeds", headers=OPERATOR)).json()
    assert [(f["code"], f["stage"], f["connector"], f["source_name"]) for f in feeds] == [
        ("upload_a", "candidate", "dropzone", "Upload A")]
    page = await client.get(f"/api/amaze/feeds/{body['feed_id']}", headers=OPERATOR)
    assert page.status_code == 200 and page.json()["source"]["provider"] == "Uploaded by Olive Operator"
    assert page.json()["source"]["conditions"]["licence"] == "Internal use only"

    profiled = await client.post(f"/api/amaze/feeds/{body['feed_id']}/profile", headers=TEAM)
    assert profiled.status_code == 200, profiled.text
    assert profiled.json()["result"]["outcome"] == "profiled" and profiled.json()["result"]["profile"]["row_count"] == 25

    subjects = db_session.execute(text("SELECT recipient, subject FROM notifications "
                                       "WHERE subject LIKE 'File ready%' ORDER BY recipient")).all()
    assert subjects == [("dev-backend-team", "File ready to profile: upload_a"),
                        ("dev-operator", "File ready to profile: upload_a")]


async def test_the_operator_types_the_conditions(client):
    stored = (await upload(client, csv_bytes())).json()
    response = await confirm(client, stored["upload_id"], licence="Vendor licence", permitted_use="Research",
                             redistribution="Internal")
    page = await client.get(f"/api/amaze/feeds/{response.json()['feed_id']}", headers=OPERATOR)
    assert page.json()["source"]["conditions"] == {
        "licence": "Vendor licence", "permitted_use": "Research", "redistribution": "Internal",
        "external_approvals": []}


async def test_confirm_with_a_sheet_the_workbook_lacks_is_422_not_a_table(client):
    stored = (await upload(client, workbook(), "book.xlsx")).json()
    response = await confirm(client, stored["upload_id"], filename="book.xlsx", sheet="Nope")
    assert response.status_code == 422 and response.json()["outcome"] == "not_a_table"
    assert (await client.get("/api/amaze/feeds", headers=OPERATOR)).json() == []


async def test_a_taken_feed_code_is_409_and_confirming_twice_is_409(client):
    stored = (await upload(client, csv_bytes())).json()
    assert (await confirm(client, stored["upload_id"])).status_code == 200
    again = await confirm(client, stored["upload_id"])
    assert again.status_code == 409 and "upload_a" in again.json()["detail"]
    other = (await upload(client, csv_bytes())).json()
    assert (await confirm(client, other["upload_id"])).status_code == 409
    assert len((await client.get("/api/amaze/feeds", headers=OPERATOR)).json()) == 1


async def test_an_unknown_upload_is_404_and_a_bad_feed_code_is_422(client):
    assert (await confirm(client, uuid.uuid4())).status_code == 404
    stored = (await upload(client, csv_bytes())).json()
    for code in ("Bad Code", "1abc", ""):
        assert (await confirm(client, stored["upload_id"], feed_code=code)).status_code == 422
    assert (await confirm(client, stored["upload_id"], feed_name="  ")).status_code == 422
    assert (await client.post(f"{URL}/not-a-uuid/confirm", headers=OPERATOR, json={})).status_code == 422


# ---------------------------------------------------------------- roles


@pytest.mark.parametrize("headers", [TEAM, OPERATOR])
async def test_the_backend_team_and_the_operator_may_upload(client, headers):
    assert (await upload(client, csv_bytes(), headers=headers)).status_code == 200


@pytest.mark.parametrize(("headers", "status"), [(STEWARD, 403), (REQUESTER, 403), ({}, 401),
                                                 ({"X-Dev-User": "dev-operations"}, 403)])
async def test_every_other_role_is_refused(client, headers, status):
    assert (await upload(client, csv_bytes(), headers=headers)).status_code == status
    stored = (await upload(client, csv_bytes())).json()
    assert (await confirm(client, stored["upload_id"], headers=headers)).status_code == status
    assert (await client.post(f"{URL}/{stored['upload_id']}/inspect", headers=headers,
                              json={"filename": "a.csv", "sheet": None, "header_row": None})).status_code == status
    assert (await client.post(f"{URL}/{stored['upload_id']}/extract", headers=headers,
                              json={"filename": "a.csv"})).status_code == status


# ---------------------------------------------------------------- scanned PDFs: extract, then confirm (task 2.3)


async def extract(client, upload_id, headers=OPERATOR, filename="scan.pdf"):
    return await client.post(f"{URL}/{upload_id}/extract", headers=headers, json={"filename": filename})


async def test_an_image_pdf_is_extracted_and_confirmed_into_a_feed(client, db_session):
    stored = (await upload(client, image_pdf(2), "scan.pdf")).json()
    assert stored["outcome"] == "needs_image_extraction"
    db_session.commit()  # the model call is made with the session in no transaction
    extracted = await extract(client, stored["upload_id"], headers=TEAM)
    assert extracted.status_code == 200, extracted.text
    body = extracted.json()
    assert body["outcome"] == "table_extracted" and body["proposal_id"]
    assert (body["header"], body["row_count"], body["confidence"]) == (["id", "name", "qty"], 3, 0.9)
    assert body["rows"][2] == ["3", "Cy", None] and body["notes"]

    response = await confirm(client, stored["upload_id"], filename="scan.pdf", extracted_proposal_id=body["proposal_id"])
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "uploaded" and response.json()["row_count"] == 3
    status = db_session.execute(text("SELECT status FROM proposals WHERE id = :p"), {"p": body["proposal_id"]}).scalar_one()
    assert status == "approved"
    profiled = await client.post(f"/api/amaze/feeds/{response.json()['feed_id']}/profile", headers=TEAM)
    assert profiled.json()["result"]["outcome"] == "profiled" and profiled.json()["result"]["profile"]["row_count"] == 3


async def test_confirm_of_an_image_pdf_without_the_extraction_is_422_and_a_decided_proposal_is_409(client, db_session):
    stored = (await upload(client, image_pdf(1), "scan.pdf")).json()
    bare = await confirm(client, stored["upload_id"], filename="scan.pdf")
    assert bare.status_code == 422 and bare.json()["outcome"] == "not_a_table"
    db_session.commit()
    proposal = (await extract(client, stored["upload_id"])).json()["proposal_id"]
    assert (await confirm(client, stored["upload_id"], filename="scan.pdf",
                          extracted_proposal_id=proposal)).status_code == 200
    again = await confirm(client, stored["upload_id"], filename="scan.pdf", feed_code="upload_b",
                          extracted_proposal_id=proposal)
    assert again.status_code == 409
    unknown = await confirm(client, stored["upload_id"], filename="scan.pdf", feed_code="upload_c",
                            extracted_proposal_id=str(uuid.uuid4()))
    assert unknown.status_code == 404
    csv = (await upload(client, csv_bytes())).json()
    assert (await confirm(client, csv["upload_id"], extracted_proposal_id=proposal)).status_code == 422


async def test_extract_of_a_csv_is_422_of_an_unknown_upload_404(client, db_session):
    stored = (await upload(client, csv_bytes())).json()
    db_session.commit()
    csv = await extract(client, stored["upload_id"], filename="a.csv")
    assert csv.status_code == 422 and csv.json()["outcome"] == "not_a_table"
    assert (await extract(client, uuid.uuid4())).status_code == 404


@pytest.mark.parametrize(("error", "status", "key"), [("unavailable", 503, "reason"), ("refused", 422, "reason")])
async def test_a_model_that_does_not_answer_or_refuses_writes_nothing(client, db_session, error, status, key):
    from apps.api.deps import get_llm_provider_port
    from apps.api.main import app
    from ports.llm.base import LLMRefused, LLMUnavailable

    class Failing:
        name = "mock"

        def complete(self, **kwargs):
            raise LLMUnavailable("timeout") if error == "unavailable" else LLMRefused("content_filter")

    stored = (await upload(client, image_pdf(1), "scan.pdf")).json()
    db_session.commit()
    app.dependency_overrides[get_llm_provider_port] = lambda: Failing()
    response = await extract(client, stored["upload_id"])
    assert response.status_code == status and key in response.json()
    assert db_session.execute(text("SELECT count(*) FROM proposals")).scalar_one() == 0
    assert db_session.execute(text("SELECT count(*) FROM proposal_log")).scalar_one() == 0


# ---------------------------------------------------------------- recurring files (phase 7.8, task 4.4)


def files_url(feed_id) -> str:
    return f"/api/amaze/feeds/{feed_id}/files"


async def confirmed_feed(client) -> str:
    stored = (await upload(client, csv_bytes(3))).json()
    return (await confirm(client, stored["upload_id"])).json()["feed_id"]


async def test_confirm_copies_the_original_into_the_drop_folder(client, dropzone):
    await confirmed_feed(client)
    [first] = list((dropzone / "upload_a").iterdir())
    assert first.name.endswith("_a.csv") and first.read_bytes() == csv_bytes(3)


async def test_a_re_upload_writes_the_file_into_the_feeds_drop_folder_and_loads_nothing(client, dropzone, db_session):
    feed_id = await confirmed_feed(client)
    data = csv_bytes(5)
    response = await client.post(files_url(feed_id), params={"filename": "Week 2.csv"}, content=data,
                                 headers={**OPERATOR, **OCTET})
    assert response.status_code == 200, response.text
    stored_as = response.json()["stored_as"]
    assert stored_as.endswith("_Week_2.csv") and (dropzone / "upload_a" / stored_as).read_bytes() == data
    assert len(list((dropzone / "upload_a").iterdir())) == 2  # the first file and this one; no .part left
    assert db_session.execute(text("SELECT count(*) FROM loads")).scalar_one() == 0
    assert db_session.execute(text("SELECT count(*) FROM source_fetches")).scalar_one() == 1  # only the first


async def test_a_re_upload_for_a_feed_that_is_not_a_dropzone_feed_is_409(client, dropzone, db_session):
    from core.repositories.feeds import FeedRepository

    feed = FeedRepository(db_session).create_candidate(
        code="syn_file", name="File feed", source_code="syn_file", connector="local_file", source_ref="files/a.csv")
    db_session.commit()
    response = await client.post(files_url(feed.id), params={"filename": "a.csv"}, content=csv_bytes(),
                                 headers={**OPERATOR, **OCTET})
    assert response.status_code == 409
    assert not dropzone.exists() or list(dropzone.rglob("*")) == []


async def test_a_re_upload_over_the_limit_is_413_and_an_unsupported_name_422_and_an_unknown_feed_404(client, dropzone):
    feed_id = await confirmed_feed(client)
    big = await client.post(files_url(feed_id), params={"filename": "a.csv"}, content=b"a,b\n" + b"1,2\n" * 300_000,
                            headers={**OPERATOR, **OCTET})
    assert big.status_code == 413 and big.json()["outcome"] == "too_large"
    odd = await client.post(files_url(feed_id), params={"filename": "a.exe"}, content=b"x",
                            headers={**OPERATOR, **OCTET})
    assert odd.status_code == 422 and odd.json()["outcome"] == "unsupported_format"
    missing = await client.post(files_url(uuid.uuid4()), params={"filename": "a.csv"}, content=csv_bytes(),
                                headers={**OPERATOR, **OCTET})
    assert missing.status_code == 404
    assert len(list((dropzone / "upload_a").iterdir())) == 1  # only the first file


@pytest.mark.parametrize(("headers", "status"), [(TEAM, 200), (STEWARD, 403), (REQUESTER, 403), ({}, 401)])
async def test_only_the_backend_team_re_uploads(client, headers, status):
    feed_id = await confirmed_feed(client)
    response = await client.post(files_url(feed_id), params={"filename": "a.csv"}, content=csv_bytes(),
                                 headers={**headers, **OCTET})
    assert response.status_code == status


# ---------------------------------------------------------------- phase 7.21: recurring or one-time (T2)

WEEKLY = {"kind": "recurring", "frequency": "weekly", "weekday": 4, "expected_time": "18:00:00", "grace_minutes": 30,
          "timezone": "Europe/London", "notify_owner": False}


async def feed_codes(client) -> list[str]:
    return [f["code"] for f in (await client.get("/api/amaze/feeds", headers=OPERATOR)).json()]


async def test_the_question_is_required_to_create_the_feed(client):
    stored = (await upload(client, csv_bytes())).json()
    payload = {"filename": "a.csv", "feed_code": "upload_a", "feed_name": "Upload A", "sheet": None, "header_row": None}
    response = await client.post(f"{URL}/{stored['upload_id']}/confirm", json=payload, headers=OPERATOR)
    assert response.status_code == 422 and "schedule" in response.text
    assert await feed_codes(client) == []


async def test_the_answer_is_written_with_the_feed_as_version_1(client):
    stored = (await upload(client, csv_bytes())).json()
    feed_id = (await confirm(client, stored["upload_id"], schedule=WEEKLY)).json()["feed_id"]
    page = (await client.get(f"/api/amaze/feeds/{feed_id}", headers=OPERATOR)).json()
    assert page["schedule"]["version"] == 1 and [s["version"] for s in page["schedules"]] == [1]
    assert (page["schedule"]["frequency"], page["schedule"]["weekday"], page["schedule"]["expected_time"],
            page["schedule"]["grace_minutes"], page["schedule"]["timezone"], page["schedule"]["notify_owner"]) == (
        "weekly", 4, "18:00:00", 30, "Europe/London", False)
    assert page["schedule"]["decided_by"] == "dev-operator"


async def test_a_one_time_load_is_an_answer(client):
    stored = (await upload(client, csv_bytes())).json()
    feed_id = (await confirm(client, stored["upload_id"], schedule={"kind": "one_time"})).json()["feed_id"]
    schedule = (await client.get(f"/api/amaze/feeds/{feed_id}/schedule", headers=OPERATOR)).json()["current"]
    assert schedule["kind"] == "one_time" and schedule["frequency"] is None and schedule["expected_time"] is None


@pytest.mark.parametrize("schedule", [
    {"kind": "recurring", "frequency": "weekly", "expected_time": "07:00:00"},
    {"kind": "recurring", "frequency": "monthly", "day_of_month": "31", "expected_time": "07:00:00"},
    {"kind": "recurring", "frequency": "daily"},
    {"kind": "one_time", "frequency": "daily"},
    {"kind": "recurring", "frequency": "daily", "expected_time": "07:00:00", "timezone": "Nowhere/Land"},
    {},
])
async def test_an_answer_that_does_not_fit_creates_no_feed(client, schedule):
    stored = (await upload(client, csv_bytes())).json()
    assert (await confirm(client, stored["upload_id"], schedule=schedule)).status_code == 422
    assert await feed_codes(client) == []


async def test_change_schedule_is_a_new_version_and_the_same_one_is_unchanged(client):
    stored = (await upload(client, csv_bytes())).json()
    feed_id = (await confirm(client, stored["upload_id"])).json()["feed_id"]
    url = f"/api/amaze/feeds/{feed_id}/schedule"
    same = await client.post(url, json=SCHEDULE, headers=TEAM)
    assert same.status_code == 200 and same.json()["outcome"] == "schedule_unchanged"
    changed = await client.post(url, json=WEEKLY, headers=TEAM)
    assert changed.status_code == 200 and changed.json()["outcome"] == "schedule_set"
    assert changed.json()["schedule"]["version"] == 2 and changed.json()["schedule"]["decided_by"] == "dev-backend-team"
    view = (await client.get(url, headers=STEWARD)).json()  # any backend role reads
    assert [v["version"] for v in view["versions"]] == [1, 2] and view["current"]["frequency"] == "weekly"
    assert (await client.post(url, json={"kind": "recurring", "frequency": "weekly"}, headers=TEAM)).status_code == 422


@pytest.mark.parametrize(("headers", "status"), [(TEAM, 200), (STEWARD, 403), (REQUESTER, 403), ({}, 401)])
async def test_only_the_backend_team_changes_a_schedule(client, headers, status):
    stored = (await upload(client, csv_bytes())).json()
    feed_id = (await confirm(client, stored["upload_id"])).json()["feed_id"]
    assert (await client.post(f"/api/amaze/feeds/{feed_id}/schedule", json=WEEKLY, headers=headers)).status_code == status


async def test_a_feed_with_no_schedule_says_so_and_an_unknown_feed_is_404(client, db_session):
    from core.repositories.feeds import FeedRepository

    feed = FeedRepository(db_session).create_candidate(code="syn_old_feed", name="Old feed")
    db_session.commit()  # the endpoints end their read transactions
    page = (await client.get(f"/api/amaze/feeds/{feed.id}", headers=OPERATOR)).json()
    assert page["schedule"] is None and page["schedules"] == []
    assert (await client.get(f"/api/amaze/feeds/{feed.id}/schedule", headers=OPERATOR)).json() == {
        "current": None, "versions": []}
    assert (await client.post(f"/api/amaze/feeds/{feed.id}/schedule", json=WEEKLY, headers=TEAM)).status_code == 200
    assert (await client.get(f"/api/amaze/feeds/{uuid.uuid4()}/schedule", headers=OPERATOR)).status_code == 404
