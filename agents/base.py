"""agents/base.Agent: the only path to the model (CLAUDE.md rules 1 and 4; phase 3 plan, assumptions 19-23).

A subclass declares what it is and how its output becomes proposal drafts:

    name, prompt_id, autonomy (A1 | A2), approver_roles (an ordered tuple of roles, each once; () for a
    read-only agent), input_schema, output_schema, and optionally to_proposals(agent_input, output).

run() is defined here only. A subclass may override only to_proposals and (phase 7.14) roles_for, which gives one
draft a prefix of the declared roles, checked when the proposals are made, after the call. One that defines run() or any
other method of this class (_log_call, _own_transaction, __init__ ...) is refused when the class is
created (independent review, fix 9). It may add helpers of its own. run() does,
in this order:
    1. render the prompt (agents/prompt_loader.py) with the input;
    2. call LLMProvider.complete();
    3. check the output and store the rendered messages and the input in the BlobStore
       (agent-inputs/<agent>/<id>.json);
    4. ProposalLog.record(): the call row, whose input_ref is that key, committed at once in its own
       short transaction (with a discard row if step 3 failed);
    5. before_write(session), if given: the caller locks and checks again;
    6. create the subclass's drafts as proposals, each pointing at the call row, with the agent's own
       autonomy and approver role, in the caller's transaction (if 5 or 6 fails: a discard row,
       committed the same way);
    7. return an AgentRun.
A call that fails before answering writes nothing. Every answered call has exactly one call row.
The caller's own transaction is flushed, never committed.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from types import FunctionType
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from agents.prompt_loader import PromptLibrary
from core.domain.proposal import Autonomy, ProposalKind
from core.domain.sourcing import AgentRun
from core.proposal_log import ProposalLog
from core.repositories.proposals import ProposalRepository
from ports.identity.base import ROLES, Role
from ports.llm.base import LLMProvider
from ports.storage.base import BlobStore

_DECLARED = ("name", "prompt_id", "autonomy", "approver_roles", "input_schema", "output_schema")


class ReadOnlyAgentProposed(RuntimeError):
    """A read-only agent (approver_roles ()) returned proposal drafts."""


class RolesNotAPrefix(RuntimeError):
    """roles_for() gave roles that are not a non-empty prefix of the agent's declared approver_roles (phase 7.14)."""


class SessionInTransaction(RuntimeError):
    """run() was given a session that is in a transaction: it would hold a database connection while the
    model answers. The caller reads, ends that transaction, then calls run() (independent review, fix 5)."""


class AgentProposal(BaseModel):
    """One proposal draft from an agent. Autonomy and approver come from the agent, not from here."""

    model_config = ConfigDict(extra="forbid")

    kind: ProposalKind
    payload: dict[str, Any]
    requirement_id: UUID | None = None
    feed_id: UUID | None = None


class Agent:
    name: ClassVar[str]
    prompt_id: ClassVar[str]
    autonomy: ClassVar[Autonomy]
    approver_roles: ClassVar[tuple[Role, ...]]  # in approval order; () for a read-only agent
    input_schema: ClassVar[type[BaseModel]]
    output_schema: ClassVar[type[BaseModel]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        overridden = sorted(name for name in cls.__dict__ if name in _SEALED)
        if overridden:  # independent review, fix 9: _log_call or _own_transaction would make an unlogged call
            name = "run" if "run" in overridden else overridden[0]
            raise TypeError(f"{cls.__name__} overrides {name}(): a subclass may override only to_proposals; "
                            "Agent.run() is the only path to the model")
        missing = [attr for attr in _DECLARED if not hasattr(cls, attr)]
        if missing:
            raise TypeError(f"{cls.__name__} must declare {', '.join(missing)}")
        if cls.autonomy not in ("A1", "A2"):
            raise TypeError(f"{cls.__name__}.autonomy must be A1 or A2, not {cls.autonomy!r}")
        roles = cls.approver_roles
        if (not isinstance(roles, tuple) or any(r not in ROLES for r in roles) or len(set(roles)) != len(roles)):
            raise TypeError(f"{cls.__name__}.approver_roles must be a tuple of roles, each once (() for a "
                            f"read-only agent), not {roles!r}")
        for attr in ("input_schema", "output_schema"):
            schema = getattr(cls, attr)
            if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
                raise TypeError(f"{cls.__name__}.{attr} must be a Pydantic model class")
        if not roles and cls.to_proposals is not Agent.to_proposals:
            raise TypeError(f"{cls.__name__} is read-only (approver_roles ()) and cannot define to_proposals")

    def __init__(self, *, provider: LLMProvider, blob_store: BlobStore, prompts: PromptLibrary | None = None):
        self._provider = provider
        self._blobs = blob_store
        self._prompts = prompts or PromptLibrary()

    def to_proposals(self, agent_input: BaseModel, output: BaseModel) -> list[AgentProposal]:
        """The proposal drafts for this output. None by default (a read-only agent)."""
        return []

    def roles_for(self, draft: AgentProposal) -> tuple[Role, ...]:
        """Phase 7.14: who decides this draft, in order: a non-empty prefix of approver_roles (checked in run(),
        where the proposals are made, after the call). By default all of them, so an agent that does not override it
        is unchanged."""
        return self.approver_roles

    def run(self, session: Session, agent_input: BaseModel, *,
            before_write: Callable[[Session], None] | None = None,
            after_answer: Callable[[Any], Any] | None = None) -> AgentRun:
        """Every answered model call has exactly one proposal_log row, committed at once in its own short
        transaction, before the caller writes anything. If the answer is then discarded (the output check,
        the input file, before_write or the proposals fail), a discard row pointing at it is committed the
        same way, and the error is raised again; the caller writes nothing.

        before_write(session) runs after the call row is committed and before the proposals: the caller
        locks what it will change and checks it again there.

        after_answer(output) -> output (phase 6 plan, assumption 9) runs after the call row is committed and
        before before_write, with the session in no transaction (checked): code fills what the model must not
        (the back-test of step 09). It returns a copy of the same output type; the proposals are made from that
        copy, and the call row keeps what the model said. If it fails, the answer is discarded like a failure of
        before_write.

        The model call never touches the session. The caller ends its read transaction before calling
        run(), so no database connection is held while the model answers (review fix 2); a session still
        in a transaction is refused with SessionInTransaction before the call (independent review, fix 5)."""
        if session.in_transaction():
            raise SessionInTransaction(f"{self.name}: end the read transaction before the model call (commit or "
                                       "roll back), so no database connection is held while the model answers")
        if not isinstance(agent_input, self.input_schema):
            raise TypeError(f"{self.name} takes {self.input_schema.__name__}, not {type(agent_input).__name__}")
        prompt = self._prompts.get(self.prompt_id)
        messages = prompt.render(agent_input)

        result = self._provider.complete(prompt_id=prompt.prompt_id, prompt_version=prompt.version,
                                         messages=messages, response_schema=self.output_schema)

        input_ref = f"agent-inputs/{self.name}/{uuid.uuid4()}.json"
        failure: Exception | None = None
        try:
            if not isinstance(result.output, self.output_schema):
                raise TypeError(f"{self.name}: the provider returned {type(result.output).__name__}, "
                                f"not {self.output_schema.__name__}")
            stored = {"agent": self.name, "prompt_id": prompt.prompt_id, "prompt_version": prompt.version,
                      "input": agent_input.model_dump(mode="json"), "messages": [m.model_dump() for m in messages]}
            self._blobs.put(input_ref, json.dumps(stored, sort_keys=True).encode("utf-8"))
        except Exception as exc:  # the call is logged first, then discarded, then this is raised
            failure = exc
        log_id = self._log_call(session, input_ref, result, discard=failure)
        if failure is not None:
            raise failure

        try:
            output = result.output
            if after_answer is not None:
                if session.in_transaction():
                    raise SessionInTransaction(f"{self.name}: after_answer runs outside any transaction")
                output = after_answer(output)
                if not isinstance(output, self.output_schema):
                    raise TypeError(f"{self.name}: after_answer returned {type(output).__name__}, "
                                    f"not {self.output_schema.__name__}")
            if before_write is not None:
                before_write(session)
            drafts = self.to_proposals(agent_input, output)
            if drafts and not self.approver_roles:
                raise ReadOnlyAgentProposed(f"{self.name} is read-only and cannot create proposals")
            repository = ProposalRepository(session)
            proposals = [
                repository.create(proposal_log_id=log_id, autonomy=self.autonomy,
                                  required_approver_roles=self._roles(draft), **draft.model_dump())
                for draft in drafts
            ]
        except Exception as exc:
            self._own_transaction(session, lambda log: log.discard(proposal_log_id=log_id, reason=_reason(exc)))
            raise
        return AgentRun(log_id=log_id, input_ref=input_ref, output=output, proposals=proposals)

    def _roles(self, draft: AgentProposal) -> list[Role]:
        roles = tuple(self.roles_for(draft))
        if not roles or roles != self.approver_roles[:len(roles)]:
            raise RolesNotAPrefix(f"{self.name}: roles {roles!r} are not a prefix of {self.approver_roles!r}")
        return list(roles)

    def _log_call(self, session: Session, input_ref: str, result, *, discard: Exception | None) -> UUID:
        """The call row (and its discard row, if the answer is already discarded), committed at once."""
        def write(log: ProposalLog) -> UUID:
            entry = log.record(agent_name=self.name, input_ref=input_ref, result=result)
            if discard is not None:
                log.discard(proposal_log_id=entry.id, reason=_reason(discard))
            return entry.id

        return self._own_transaction(session, write)

    @staticmethod
    def _own_transaction(session: Session, work):
        """A short transaction of its own on the caller's bind, committed at once. On the API's engine that
        is its own connection; in the tests' shared connection, a savepoint rolled back with the test."""
        with Session(bind=session.get_bind(), join_transaction_mode="create_savepoint") as own:
            outcome = work(ProposalLog(own))
            own.commit()
            return outcome


# Every method of the base class but to_proposals: a subclass that defines one is refused at creation.
_SEALED = frozenset(name for name, value in vars(Agent).items()
                    if isinstance(value, (FunctionType, staticmethod, classmethod, property))
                    and name not in ("to_proposals", "roles_for"))  # roles_for: phase 7.14


def _reason(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"
