"""Write configuration the way phase 5 does (phase 5 plan, assumption 40).

From migration 0016 a mapping row must match its approved mapping proposal (same feed, same column, one of
its candidates, the next version, a registered feed), and a non-seed glossary row must be one of its
approved glossary_element proposal's options, written by the glossary owner who approved it. These
helpers make a proposal of the real shape on a logged call, have the right dev user decide it, and write
the row through the repository. No trigger is bypassed. Each helper flushes; the test's session rolls back.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel

from core.domain.configuration import Mapping
from core.domain.glossary import GlossaryElement, GlossaryElementDraft
from core.domain.mapping import ElementOption, GlossaryProposalPayload, MappingProposalPayload
from core.domain.proposal import Proposal
from core.proposal_log import ProposalLog
from core.repositories.configuration import MappingRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from tests.conftest import dev_user_for
from tests.onboarded import registered_feed

GLENN = "dev-glossary-owner"


class _Answer(BaseModel):
    note: str = "synthetic"


def logged_call(session, agent_name: str, prompt_id: str):
    from ports.llm.base import LLMResult

    result = LLMResult(output=_Answer(), provider="test", model="test", model_version="0", prompt_id=prompt_id,
                       prompt_version="v1", latency_ms=0, raw_ref=None)
    return ProposalLog(session).record(agent_name=agent_name, input_ref=f"test://{prompt_id}", result=result)


def registered(session, feed_id: UUID) -> None:
    """Register the feed through the real onboarding chain if it is still a candidate."""
    if FeedRepository(session).get(feed_id).stage == "candidate":
        registered_feed(session, feed_id)


def a_registered_feed(session, code: str | None = None) -> UUID:
    code = code or f"syn_configured_{uuid.uuid4().hex[:8]}"
    feed = FeedRepository(session).create_candidate(code=code, name=code.replace("_", " ").capitalize())
    registered_feed(session, feed.id)
    return feed.id


# ---------------------------------------------------------------- mappings


def mapping_proposal(session, feed_id: UUID, source_field: str, candidates: Sequence[str], *,
                     decision: str | None = None, note: str | None = None) -> Proposal:
    """A mapping proposal of the real shape; pending unless decision is given (by the dev data steward)."""
    registered(session, feed_id)
    payload = MappingProposalPayload(source_field=source_field, column_type=None, candidates=list(candidates),
                                     recommended=candidates[0] if len(candidates) == 1 else None,
                                     reason="Synthetic reason.")
    repo = ProposalRepository(session)
    proposal = repo.create(proposal_log_id=logged_call(session, "MappingAgent", "mapping").id, kind="mapping",
                           payload=payload.model_dump(mode="json"), autonomy="A2",
                           required_approver_roles=["data_steward"], feed_id=feed_id)
    if decision is not None:
        repo.record_decision(proposal.id, user=dev_user_for("data_steward"), decision=decision, note=note)
    return repo.get(proposal.id)


def approved_mapping(session, feed_id: UUID, source_field: str, element_key: str, *,
                     candidates: Sequence[str] | None = None) -> Mapping:
    """The column's next mapping version: a proposal offering element_key (and candidates), approved, written."""
    offered = list(candidates) if candidates else [element_key]
    proposal = mapping_proposal(session, feed_id, source_field, offered, decision="approved")
    return MappingRepository(session).add_version(feed_id=feed_id, source_field=source_field,
                                                  element_key=element_key, proposal_id=proposal.id)


# ---------------------------------------------------------------- glossary elements


def new_element(key: str, *, name: str | None = None, domain: str = "pricing", data_type: str = "string",
                definition: str = "Synthetic definition.", aliases: Sequence[str] = ()) -> ElementOption:
    element = GlossaryElementDraft(key=key, name=name or key.replace("_", " ").capitalize(), domain=domain,
                                   data_type=data_type, definition=definition, aliases=list(aliases))
    return ElementOption(kind="new_element", key=key, element=element)


def alias(key: str, alias_: str) -> ElementOption:
    return ElementOption(kind="alias", key=key, alias=alias_)


def glossary_proposal(session, options: Sequence[ElementOption], *, feed_id: UUID | None = None,
                      column: str | None = None, outcome: str | None = None,
                      decision: str | None = None) -> Proposal:
    """A glossary_element proposal of the real shape on a registered feed; decided by Glenn if asked."""
    feed_id = feed_id or a_registered_feed(session)
    outcome = outcome or ("conflict_to_owner" if len(options) != 1 else
                          "proposed" if options[0].kind == "new_element" else "duplicate_alias")
    payload = GlossaryProposalPayload(source_field=column or f"col_{uuid.uuid4().hex[:6]}", outcome=outcome,
                                      options=list(options),
                                      recommended=None if outcome == "conflict_to_owner" else 0,
                                      reason="Synthetic reason.", run_id=uuid.uuid4())
    repo = ProposalRepository(session)
    proposal = repo.create(proposal_log_id=logged_call(session, "GlossaryBuilderAgent", "glossary_builder").id,
                           kind="glossary_element", payload=payload.model_dump(mode="json"), autonomy="A2",
                           required_approver_roles=["glossary_owner"], feed_id=feed_id)
    if decision is not None:
        repo.record_decision(proposal.id, user=dev_user_for("glossary_owner"), decision=decision)
    return repo.get(proposal.id)


def approved_element(session, option: ElementOption, *, feed_id: UUID | None = None,
                     column: str | None = None) -> GlossaryElement:
    """The option written from an approved proposal that offers it, by the glossary owner who approved it."""
    proposal = glossary_proposal(session, [option], feed_id=feed_id, column=column, decision="approved")
    repo = GlossaryRepository(session)
    repo.lock()
    return repo.add_version(proposal_id=proposal.id, created_by=GLENN, option=option)
