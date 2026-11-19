"""TableExtractorAgent (phase 7.8, task 2.3, decision D4): a scanned PDF's pages go to the model as images; the
answer is one extracted_table proposal the operator confirms.

Covers the image message parts (ports/llm/base.py), the prompt loader keeping image bytes out of the JSON block, the
mock's recorded tables (fixtures/llm/table_extractor/<first page hash>.json) and the proposal's kind and payload.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.prompt_loader import PromptLibrary
from agents.table_extractor import TableExtractorAgent
from core.domain.extraction import ExtractedTable, TableExtractionInput
from core.intake import render_pages_png
from ports.config import REPO_ROOT
from ports.llm.base import ImagePart, Message, TextPart
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.step_chains import end_read
from tests.test_intake_pdf import image_pdf

RECORDED = REPO_ROOT / "fixtures" / "llm" / "table_extractor"


def extraction_input(pages: int = 1, *, upload_id=None, filename: str = "scan.pdf") -> TableExtractionInput:
    numbers = list(range(1, pages + 1))
    pngs = render_pages_png(image_pdf(pages), numbers)
    return TableExtractionInput(
        upload_id=upload_id or uuid.uuid4(), filename=filename, pages=numbers,
        page_images=[base64.b64encode(p).decode("ascii") for p in pngs],
        page_hashes=[hashlib.sha256(p).hexdigest() for p in pngs])


def test_the_agents_declaration():
    assert (TableExtractorAgent.name, TableExtractorAgent.prompt_id, TableExtractorAgent.autonomy,
            TableExtractorAgent.approver_roles) == ("TableExtractorAgent", "table_extractor", "A2", ("backend_team",))
    assert TableExtractorAgent.input_schema is TableExtractionInput
    assert TableExtractorAgent.output_schema is ExtractedTable


def test_the_synthetic_image_page_renders_to_the_same_bytes_twice_and_has_a_recorded_table():
    first, second = extraction_input(), extraction_input()
    assert first.page_hashes == second.page_hashes and first.page_images == second.page_images
    assert (RECORDED / f"{first.page_hashes[0]}.json").is_file(), "record the table under the first page's hash"


# ---------------------------------------------------------------- the message


def test_the_user_message_carries_one_image_part_per_page_and_the_input_block_holds_no_image_text():
    agent_input = extraction_input(2)
    messages = PromptLibrary().get("table_extractor").render(agent_input)
    system, user = messages
    assert system.role == "system" and isinstance(system.content, str)
    assert user.role == "user" and isinstance(user.content, list)
    *images, last = user.content
    assert [type(p) for p in images] == [ImagePart, ImagePart] and isinstance(last, TextPart)
    assert [p.data_b64 for p in images] == agent_input.page_images
    assert all(p.media_type == "image/png" and p.type == "image" for p in images)
    block = json.loads(last.text.rsplit("```json\n", 1)[1].rsplit("\n```", 1)[0])
    assert "page_images" not in block and block["page_hashes"] == agent_input.page_hashes
    assert block["pages"] == [1, 2] and block["filename"] == "scan.pdf"
    assert not any(image in last.text for image in agent_input.page_images)
    assert last.text.rstrip().endswith("```")


def test_a_text_message_still_reads_as_text():
    message = Message(role="user", content="plain")
    assert message.content == "plain" and message.text() == "plain"
    parts = Message(role="user", content=[ImagePart(data_b64="AAAA"), TextPart(text="one"), TextPart(text="two")])
    assert parts.text() == "two"  # the last text part
    assert Message.model_validate(parts.model_dump()) == parts


# ---------------------------------------------------------------- the mock and the proposal


def test_the_recorded_table_is_returned_as_one_extracted_table_proposal(db_session, tmp_path):
    agent = TableExtractorAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))
    agent_input = extraction_input(2)
    end_read(db_session)
    run = agent.run(db_session, agent_input)
    assert isinstance(run.output, ExtractedTable)
    assert run.output.header == ["id", "name", "qty"] and run.output.confidence == 0.9
    [proposal] = run.proposals
    assert (proposal.kind, proposal.feed_id, proposal.requirement_id, proposal.required_approver_roles,
            proposal.autonomy, proposal.status) == ("extracted_table", None, None, ["backend_team"], "A2", "pending")
    assert set(proposal.payload) == {"upload_id", "filename", "pages", "page_hashes", "header", "rows", "confidence",
                                     "notes"}
    assert proposal.payload["upload_id"] == str(agent_input.upload_id) and proposal.payload["pages"] == [1, 2]
    assert proposal.payload["page_hashes"] == agent_input.page_hashes
    assert proposal.payload["rows"] == [["1", "Ann", "5"], ["2", "Bob", "6"], ["3", "Cy", None]]


def test_the_call_is_logged_and_its_stored_input_holds_the_pages(db_session, tmp_path):
    """Rule 4: the input reference is kept, with the page images (about 10 KB here, 200 KB on a real scan)."""
    blobs = LocalBlobStore(tmp_path / "blob")
    agent = TableExtractorAgent(provider=MockProvider(), blob_store=blobs)
    agent_input = extraction_input()
    end_read(db_session)
    run = agent.run(db_session, agent_input)
    stored = json.loads(blobs.get(run.input_ref))
    assert stored["agent"] == "TableExtractorAgent" and stored["prompt_id"] == "table_extractor"
    assert stored["input"]["page_images"] == agent_input.page_images and stored["input"]["page_hashes"]


def test_an_unknown_page_gives_an_empty_answer_with_confidence_zero(db_session, tmp_path):
    agent = TableExtractorAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))
    agent_input = TableExtractionInput(upload_id=uuid.uuid4(), filename="x.pdf", pages=[1],
                                       page_images=["AAAA"], page_hashes=["0" * 64])
    end_read(db_session)
    run = agent.run(db_session, agent_input)
    assert run.output.confidence == 0.0 and run.output.rows == []
    assert run.output.notes == ["mock: no recorded table for this page"]
    [proposal] = run.proposals
    assert proposal.payload["confidence"] == 0.0


def test_the_mock_reads_its_input_block_from_the_last_text_part_only():
    provider = MockProvider()
    agent_input = extraction_input()
    messages = PromptLibrary().get("table_extractor").render(agent_input)
    result = provider.complete(prompt_id="table_extractor", prompt_version="v1", messages=messages,
                               response_schema=ExtractedTable)
    assert result.output.header == ["id", "name", "qty"]
    image_only = [messages[0], Message(role="user", content=[ImagePart(data_b64="AAAA")])]
    from ports.llm.mock import MockInputMissing

    with pytest.raises(MockInputMissing):
        provider.complete(prompt_id="table_extractor", prompt_version="v1", messages=image_only,
                          response_schema=ExtractedTable)


# ---------------------------------------------------------------- the models


def test_the_input_needs_one_hash_and_one_image_per_page():
    ok = {"upload_id": uuid.uuid4(), "filename": "a.pdf", "pages": [1, 2], "page_images": ["a", "b"],
          "page_hashes": ["h1", "h2"]}
    TableExtractionInput(**ok)
    for bad in ({"page_images": ["a"]}, {"page_hashes": ["h1"]}, {"pages": [1]}, {"extra": 1}):
        with pytest.raises(ValidationError):
            TableExtractionInput(**{**ok, **bad})


@pytest.mark.parametrize("bad", [
    {"rows": [["1", "a"]]},  # a ragged row
    {"confidence": 1.5},
    {"confidence": -0.1},
    {"header": ["id", " ", "qty"]},  # a blank header cell
    {"unknown": 1},
])
def test_an_extracted_table_refuses_a_ragged_row_a_bad_confidence_and_a_blank_header(bad):
    ok = {"header": ["id", "name", "qty"], "rows": [["1", "Ann", None]], "confidence": 0.5, "notes": []}
    ExtractedTable(**ok)
    with pytest.raises(ValidationError):
        ExtractedTable(**{**ok, **bad})


def test_the_recorded_tables_are_valid_extracted_tables():
    files = sorted(Path(RECORDED).glob("*.json"))
    assert files
    for path in files:
        ExtractedTable.model_validate_json(path.read_text(encoding="utf-8"))
