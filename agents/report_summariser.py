"""ReportSummariserAgent (phase 7.15, A1, read-only: no proposal, no approver).

It sees a report's numbers (counted by code) and writes a few plain sentences about them. It informs; it changes
nothing: the numbers are stored as built before the call, the narrative is a separate field, and a narrative that states
a figure the report does not hold is refused by core/steps/reports.py. The call is logged like every model call (rule 4).
"""

from __future__ import annotations

from agents.base import Agent
from core.domain.report import ReportSummaryInput, ReportSummaryOutput


class ReportSummariserAgent(Agent):
    name = "ReportSummariserAgent"
    prompt_id = "report_summariser"
    autonomy = "A1"
    approver_roles = ()
    input_schema = ReportSummaryInput
    output_schema = ReportSummaryOutput
