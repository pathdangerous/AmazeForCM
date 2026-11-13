"""Step 01 Define requirement: save what the requester asked for, and tell the people who act on it.

Outcomes (CLAUDE.md): defined; element_not_in_glossary. The requirement is saved in both cases, with
status defined, its elements and the ticked terms (decision 3).

Notifications (in-app, inside the caller's transaction):
- every requirement: each backend_team user (decision 15);
- ticked terms: each glossary_owner user.

The step flushes but never commits; the caller owns the transaction (phase 2 plan, assumption 4).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Literal

from pydantic import Field
from sqlalchemy.orm import Session

from core.domain.requirement import Requirement
from core.repositories.requirements import RequirementRepository
from core.steps.errors import NothingToRequest
from core.steps.result import StepResult
from ports.notify.base import Notifier

TITLE_LENGTH = 120


class Defined(StepResult):
    outcome: Literal["defined"] = "defined"
    requirement: Requirement
    notified: list[str]  # user ids, so an empty list is visible


class ElementNotInGlossary(StepResult):
    outcome: Literal["element_not_in_glossary"] = "element_not_in_glossary"
    requirement: Requirement
    terms: list[str]
    notified: list[str]


DefineRequirementResult = Annotated[Defined | ElementNotInGlossary, Field(discriminator="outcome")]


def describe(requester_name: str, element_keys: Sequence[str], terms: Sequence[str]) -> str:
    """The body of a notification about a request."""
    return (f"Requested by {requester_name}.\nElements: {', '.join(element_keys) or 'none'}.\n"
            f"Terms: {', '.join(terms) or 'none'}.")


def define_requirement(session: Session, *, requester_id: str, query: str, purpose: str,
                       element_keys: Sequence[str], terms: Sequence[str], notifier: Notifier,
                       recipients: Mapping[str, Sequence[str]],
                       display_name: Callable[[str], str]) -> Defined | ElementNotInGlossary:
    """recipients: user ids per role ("backend_team", "glossary_owner")."""
    if not element_keys and not terms:
        raise NothingToRequest("A requirement needs at least one element or term")
    title = query.strip()[:TITLE_LENGTH].strip()
    requirement = RequirementRepository(session).create(
        requester_id=requester_id, title=title, description=purpose.strip(),
        element_keys=list(element_keys), terms=list(terms),
    )
    body = describe(display_name(requester_id), requirement.elements, requirement.terms)
    notified = []
    for user_id in recipients.get("backend_team", ()):
        notifier.notify(recipient=user_id, subject=f"New requirement: {title}", body=body)
        notified.append(user_id)
    if not requirement.terms:
        return Defined(requirement=requirement, notified=notified)
    for user_id in recipients.get("glossary_owner", ()):
        notifier.notify(recipient=user_id, subject="Terms not in the glossary", body=body)
        notified.append(user_id)
    return ElementNotInGlossary(requirement=requirement, terms=requirement.terms, notified=notified)
