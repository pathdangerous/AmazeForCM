"""Named errors for input a step refuses (CLAUDE.md rule 3: never a bare error for an expected case).

These are not outcomes: the request cannot be routed at all. The API turns them into 422.
"""


class StepInputError(Exception):
    """Base class for input a step refuses."""


class NothingToRequest(StepInputError):
    """No element and no term was ticked, so there is nothing to route."""


class TermInGlossary(StepInputError):
    """A ticked term is a glossary phrase; it must be requested as the element instead."""


class NotTheRequester(Exception):
    """Only the requirement's own requester decides its scope and its source (phase 3 plan, assumption 29).
    Not a StepInputError: the API answers 404, as for another requester's requirement."""


class NothingToDecide(StepInputError):
    """There is no pending proposal of this kind for the requirement."""


class ChoiceNeeded(StepInputError):
    """The proposal recommends nothing (a conflict or a tie): the person must choose one option (phase 5)."""


class ChoiceNotOffered(StepInputError):
    """The option or candidate chosen is not one the proposal offers (phase 5)."""
