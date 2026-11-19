"""A stand-in agent for step tests (phase 3 plan, assumption 22): no provider, no prompt.

It writes what Agent.run() writes (one proposal_log row, then the proposals, each pointing at it) from a
fixed output, so a step can be tested on its own. It records each input it was given and can be told
to fail, like a model call that fails.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from core.domain.sourcing import AgentRun
from core.proposal_log import ProposalLog
from core.repositories.proposals import ProposalRepository
from ports.llm.base import LLMResult


class FixedProvider:
    """An LLMProvider for step tests of a real agent: answers with output (a dict, or a function of the input
    dict that ends the user message), or raises error. Agent.run() still logs, stores and creates proposals."""

    name = "fixed"

    def __init__(self, output: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                 error: Exception | None = None):
        self.output, self.error, self.calls = output, error, 0

    def complete(self, *, prompt_id, prompt_version, messages, response_schema, temperature=0.0):
        import json

        self.calls += 1
        if self.error is not None:
            raise self.error
        data = json.loads(messages[-1].content.rsplit("```json\n", 1)[1].rsplit("\n```", 1)[0])
        raw = self.output(data) if callable(self.output) else self.output
        return LLMResult(output=response_schema.model_validate(raw), provider="fixed", model="fixed",
                         model_version="0", prompt_id=prompt_id, prompt_version=prompt_version, latency_ms=0,
                         raw_ref=None)


class StandInAgent:
    def __init__(self, *, name: str, output_schema: type[BaseModel], output: dict[str, Any] | Callable,
                 proposals: Callable[[BaseModel, BaseModel], list[dict[str, Any]]] = lambda i, o: [],
                 error: Exception | None = None, during_call: Callable[[Any], None] | None = None):
        self.name, self._schema, self._output, self._proposals, self.error = name, output_schema, output, proposals, error
        self.during_call = during_call  # runs where the model call would be; gets the session
        self.inputs: list[BaseModel] = []

    def run(self, session, agent_input: BaseModel, *, before_write=None, after_answer=None) -> AgentRun:
        self.inputs.append(agent_input)
        if self.error is not None:
            raise self.error
        if self.during_call is not None:
            self.during_call(session)
        raw = self._output(agent_input) if callable(self._output) else self._output
        output = self._schema.model_validate(raw)
        result = LLMResult(output=output, provider="stand-in", model="stand-in", model_version="0",
                           prompt_id="stand_in", prompt_version="v0", latency_ms=0, raw_ref=None)
        # As Agent.run(): the call row first, then before_write; a refusal adds a discard row.
        log = ProposalLog(session)
        entry = log.record(agent_name=self.name, input_ref="stand-in://input", result=result)
        if after_answer is not None:  # as Agent.run(): after the call row, before before_write
            output = after_answer(output)
        if before_write is not None:
            try:
                before_write(session)
            except Exception as exc:
                log.discard(proposal_log_id=entry.id, reason=f"{type(exc).__name__}: {exc}")
                raise
        repository = ProposalRepository(session)
        proposals = [repository.create(proposal_log_id=entry.id, autonomy="A2", required_approver_roles=["requester"],
                                       **draft) for draft in self._proposals(agent_input, output)]
        return AgentRun(log_id=entry.id, input_ref="stand-in://input", output=output, proposals=proposals)
