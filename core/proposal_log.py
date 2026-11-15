"""ProposalLog (CLAUDE.md rule 4).

Every model call is recorded here before its proposal is shown to a person: agent name, provider,
model and version, prompt id and version, input reference and output. The log is append-only.
Who approved or rejected a proposal is recorded in the approvals table (core/repositories/proposals.py).

record() flushes but does not commit: the caller owns the transaction.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.repositories.tables import ProposalLogEntry
from ports.llm.base import LLMResult

__all__ = ["ProposalLog", "ProposalLogEntry"]


class ProposalLog:
    def __init__(self, session: Session):
        self._session = session

    def record(self, *, agent_name: str, input_ref: str, result: LLMResult,
               discarded_reason: str | None = None) -> ProposalLogEntry:
        """discarded_reason: set when the answer was discarded (it then can have no proposal; 0011)."""
        if not agent_name.strip():
            raise ValueError("agent_name is required")
        if not input_ref.strip():
            raise ValueError("input_ref is required")
        if discarded_reason is not None and not discarded_reason.strip():
            raise ValueError("a discarded call needs its reason")
        entry = ProposalLogEntry(
            agent_name=agent_name,
            provider=result.provider,
            model=result.model,
            model_version=result.model_version,
            prompt_id=result.prompt_id,
            prompt_version=result.prompt_version,
            input_ref=input_ref,
            output=result.output.model_dump(mode="json"),
            raw_ref=result.raw_ref,
            latency_ms=result.latency_ms,
            discarded_reason=discarded_reason,
        )
        self._session.add(entry)
        self._session.flush()
        return entry
