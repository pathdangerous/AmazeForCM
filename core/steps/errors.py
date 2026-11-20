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


class ReasonNeeded(StepInputError):
    """Pausing, resuming or deleting a feed needs a reason that says why (phase 7.9); it is missing or blank."""


class NothingToDecide(StepInputError):
    """There is no pending proposal of this kind for the requirement."""


class BackToStepNeeded(StepInputError):
    """A go-live rejection names the step the feed goes back to: 05 (glossary), 06 (mapping) or 09 (rules)."""


class ChoiceNeeded(StepInputError):
    """The proposal recommends nothing (a conflict or a tie): the person must choose one option (phase 5)."""


class NoteNeeded(StepInputError):
    """A glossary or mapping proposal is rejected with a note that says why (phase 5 plan, assumptions 17
    and 28); the note is missing or blank."""


def needs_a_note(decision: str, note: str | None) -> None:
    if decision == "rejected" and not (note or "").strip():
        raise NoteNeeded("A rejection needs a note that says why")


class ChoiceNotOffered(StepInputError):
    """The option or candidate chosen is not one the proposal offers (phase 5)."""
