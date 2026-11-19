"""TableExtractorAgent (phase 7.8, task 2.3, decision D4; A2, approver: backend team).

Reads the table of a scanned (image-only) PDF from its page images, page by page, and proposes it: one
extracted_table proposal with no feed (the feed is created when the operator confirms the table). The payload keeps
the upload, the page numbers, the SHA-256 of each page image and the table as read, so the person confirms exactly
what the model answered.
"""

from __future__ import annotations

from agents.base import Agent, AgentProposal
from core.domain.extraction import ExtractedTable, TableExtractionInput


class TableExtractorAgent(Agent):
    name = "TableExtractorAgent"
    prompt_id = "table_extractor"
    autonomy = "A2"
    approver_roles = ("backend_team",)
    input_schema = TableExtractionInput
    output_schema = ExtractedTable

    def to_proposals(self, agent_input: TableExtractionInput, output: ExtractedTable) -> list[AgentProposal]:
        return [AgentProposal(kind="extracted_table", payload={
            "upload_id": str(agent_input.upload_id), "filename": agent_input.filename,
            "pages": list(agent_input.pages), "page_hashes": list(agent_input.page_hashes),
            **output.model_dump(mode="json")})]
