"""The glossary upload API (phase 7.19, T2): POST /api/amaze/glossary/uploads (the raw file as the body, like the other
upload endpoint), GET .../uploads, .../uploads/{id}, .../template, and POST .../imports/decision.

The glossary owner uploads and decides; every other backend role reads; the requester gets 403, no user 401. A refused
file is a typed body (`file_refused`) with 413 (too large) or 422; a refused row of a decision is an item of the answer."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from core.seed import seed_glossary
from ports.config import Settings
from tests.conftest import running_app

pytestmark = pytest.mark.anyio

OWNER = {"X-Dev-User": "dev-glossary-owner"}
STEWARD = {"X-Dev-User": "dev-data-steward"}
REQUESTER = {"X-Dev-User": "dev-requester"}
OCTET = {"Content-Type": "application/octet-stream"}
URL = "/api/amaze/glossary"
HEADER = "key,name,domain,data_type,definition,aliases\n"
NEW = '"best_fcf","Best free cash flow","pricing","decimal","Consensus free cash flow.","fcf_consensus"\n'
OTHER = '"best_roe","Best ROE","pricing","decimal","Consensus ROE.",""\n'


@pytest.fixture
async def client(db_session, migrated_engine):
    seed_glossary(db_session)
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None, upload_max_mb=1),
                           session=db_session) as c:
        yield c


async def upload(client, data: str | bytes, name: str = "glossary.csv", headers=OWNER):
    body = data.encode() if isinstance(data, str) else data
    return await client.post(f"{URL}/uploads", params={"filename": name}, content=body, headers={**headers, **OCTET})


async def test_the_owner_uploads_and_every_row_comes_back_labelled(client, db_session):
    before = db_session.scalar(text("SELECT count(*) FROM proposal_log"))
    response = await upload(client, HEADER + NEW + OTHER.replace("best_roe", "Bad Key"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "uploaded" and body["file_name"] == "glossary.csv" and body["proposals"] == 1
    assert body["counts"] == {"new": 1, "invalid": 1}
    assert [(r["key"], r["label"]) for r in body["rows"]] == [("best_fcf", "new"), ("Bad Key", "invalid")]
    assert db_session.scalar(text("SELECT count(*) FROM proposal_log")) == before  # no model call


async def test_a_one_row_file_is_the_add_element_form(client):
    response = await upload(client, HEADER + NEW, name="add-element.csv")
    assert response.status_code == 200 and response.json()["file_name"] == "add-element.csv"
    assert response.json()["counts"] == {"new": 1}


@pytest.mark.parametrize("headers, status", [(STEWARD, 403), (REQUESTER, 403), ({}, 401)])
async def test_only_the_glossary_owner_uploads(client, headers, status):
    assert (await upload(client, HEADER + NEW, headers=headers)).status_code == status


async def test_a_file_over_the_limit_is_413_and_names_the_reason(client):
    response = await upload(client, HEADER + NEW * 20000)  # about 2 MB; the limit is 1
    assert response.status_code == 413 and response.json()["outcome"] == "file_refused"
    assert response.json()["reason"] == "too_large"


@pytest.mark.parametrize("data, name, reason", [
    (HEADER + NEW, "glossary.pdf", "unsupported_format"),
    ("key,name\nx,y\n", "glossary.csv", "missing_columns"),
    (HEADER, "glossary.csv", "no_rows"),
])
async def test_a_refused_file_is_422_with_its_reason(client, data, name, reason):
    response = await upload(client, data, name)
    assert response.status_code == 422 and response.json()["outcome"] == "file_refused"
    assert response.json()["reason"] == reason


async def test_the_template_is_the_header_row_as_a_csv(client):
    response = await client.get(f"{URL}/template", headers=STEWARD)
    assert response.status_code == 200 and response.text.strip() == HEADER.strip()
    assert "glossary_template.csv" in response.headers["content-disposition"]


async def test_uploads_are_listed_and_one_is_shown_with_its_rows_and_decisions(client):
    made = (await upload(client, HEADER + NEW + OTHER, name="march.csv")).json()
    listed = (await client.get(f"{URL}/uploads", headers=STEWARD)).json()
    assert [(u["file_name"], u["counts"]) for u in listed] == [("march.csv", {"new": 2})]
    ids = [r["proposal_id"] for r in made["rows"]]
    await client.post(f"{URL}/imports/decision", headers=OWNER,
                      json={"proposal_ids": ids[:1], "decision": "approved"})
    page = (await client.get(f"{URL}/uploads/{made['upload_id']}", headers=STEWARD)).json()
    assert [(r["key"], r["status"], r["decided_by"]) for r in page["rows"]] == [
        ("best_fcf", "approved", "dev-glossary-owner"), ("best_roe", "pending", None)]


async def test_an_unknown_upload_is_404(client):
    response = await client.get(f"{URL}/uploads/00000000-0000-0000-0000-000000000000", headers=STEWARD)
    assert response.status_code == 404


async def test_approve_selected_writes_the_elements_and_reports_each_row(client):
    made = (await upload(client, HEADER + NEW + OTHER)).json()
    ids = [r["proposal_id"] for r in made["rows"]]
    response = await client.post(f"{URL}/imports/decision", headers=OWNER,
                                 json={"proposal_ids": ids, "decision": "approved"})
    assert response.status_code == 200, response.text
    assert [(i["outcome"], i["element"]["key"], i["element"]["version"]) for i in response.json()["items"]] == [
        ("approved", "best_fcf", 1), ("approved", "best_roe", 1)]
    element = await client.get("/api/amaze/glossary/elements/best_fcf", headers=STEWARD)
    assert element.status_code == 200 and element.json()["element"]["aliases"] == ["fcf_consensus"]


async def test_reject_selected_needs_a_note_and_writes_nothing(client):
    made = (await upload(client, HEADER + NEW)).json()
    ids = [r["proposal_id"] for r in made["rows"]]
    no_note = await client.post(f"{URL}/imports/decision", headers=OWNER, json={"proposal_ids": ids, "decision": "rejected"})
    assert no_note.status_code == 422
    rejected = await client.post(f"{URL}/imports/decision", headers=OWNER,
                                 json={"proposal_ids": ids, "decision": "rejected", "note": "Not now."})
    assert [i["outcome"] for i in rejected.json()["items"]] == ["rejected"]
    assert (await client.get("/api/amaze/glossary/elements/best_fcf", headers=STEWARD)).status_code == 404


@pytest.mark.parametrize("headers, status", [(STEWARD, 403), (REQUESTER, 403), ({}, 401)])
async def test_only_the_glossary_owner_decides(client, headers, status):
    made = (await upload(client, HEADER + NEW)).json()
    response = await client.post(f"{URL}/imports/decision", headers=headers,
                                 json={"proposal_ids": [made["rows"][0]["proposal_id"]], "decision": "approved"})
    assert response.status_code == status


async def test_a_decision_names_at_least_one_row_and_no_more_than_the_page_holds(client):
    for ids in ([], ["x"] * 2001):
        response = await client.post(f"{URL}/imports/decision", headers=OWNER,
                                     json={"proposal_ids": ids, "decision": "approved"})
        assert response.status_code == 422
