"""Named errors for expected cases (CLAUDE.md rule 3: never a bare error for an expected case)."""


class RepositoryError(Exception):
    """Base class for every expected repository failure."""


# ---- proposals and approvals


class ProposalNotFound(RepositoryError, LookupError):
    """No proposal has this id."""


class ProposalLogEntryNotFound(RepositoryError, LookupError):
    """A proposal must point at a logged model call, and none has this id."""


class ProposalAlreadyDecided(RepositoryError):
    """The proposal is already approved or rejected; nothing more can be decided."""


class NotAnApprover(RepositoryError, PermissionError):
    """The user does not hold the role required for the proposal's next step."""


class SameApproverTwice(RepositoryError, PermissionError):
    """The same person cannot decide two steps of one proposal."""


class ProposalNotApproved(RepositoryError):
    """Configuration can only be created from an approved proposal."""


class WrongProposalKind(RepositoryError):
    """The proposal is approved, but of a different kind than the configuration being created."""


class ProposalForAnotherFeed(RepositoryError):
    """The proposal was approved for a different feed than the configuration being created."""


# ---- glossary


class UnknownGlossaryElement(RepositoryError, LookupError):
    """No glossary element has this key."""


# ---- requirements and feeds


class RequirementNotFound(RepositoryError, LookupError):
    """No requirement has this id."""


class FeedNotFound(RepositoryError, LookupError):
    """No feed has this id or code."""


class DuplicateFeedCode(RepositoryError):
    """Another feed already has this code."""


class FeedHasNoOwner(RepositoryError):
    """A feed cannot be registered without an owner (step 04 outcome no_owner)."""


class FeedNotACandidate(RepositoryError):
    """Only a candidate feed can be registered."""


class FeedNotRegistered(RepositoryError):
    """Loads arrive only for registered feeds."""


# ---- loads and products


class LoadNotFound(RepositoryError, LookupError):
    """No load has this id."""


class ProductNotFound(RepositoryError, LookupError):
    """No product has this id or code."""


class DuplicateProductCode(RepositoryError):
    """Another product already has this code."""


# ---- consumer requests


class ConsumerRequestNotFound(RepositoryError, LookupError):
    """The requester has no request with this id (it may not exist, or it may be someone else's)."""
