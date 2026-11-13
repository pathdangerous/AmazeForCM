"""Named errors for input a step refuses (CLAUDE.md rule 3: never a bare error for an expected case).

These are not outcomes: the request cannot be routed at all. The API turns them into 422.
"""


class StepInputError(Exception):
    """Base class for input a step refuses."""


class NothingToRequest(StepInputError):
    """No element and no term was ticked, so there is nothing to route."""


class TermInGlossary(StepInputError):
    """A ticked term is a glossary phrase; it must be requested as the element instead."""
