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


class DuplicateFeedSource(RepositoryError):
    """Another feed already reads this source: one feed per source (phase 3 plan, assumption 11)."""


class FeedHasNoSource(RepositoryError):
    """The feed has no connector, so its source cannot be fetched."""


class FeedHasNoFile(RepositoryError):
    """The feed has no fetched file yet, so there is nothing to profile (step 02; phase 4 plan, assumption 13)."""


class FeedNotProfiled(RepositoryError):
    """The feed has no profile yet (the summary and the sign-off need one)."""


class ProfileAlreadySummarised(RepositoryError):
    """The feed's latest profile already has its ProfilerAgent summary: one per profile."""


class ProfileSuperseded(RepositoryError):
    """A newer profile of the feed was written while this one was summarised or signed off."""


class ProfileNotSummarised(RepositoryError):
    """The feed's latest profile has no ProfilerAgent summary yet: no sign-off without one (Q3)."""


class SourceRejected(RepositoryError):
    """The backend team rejected this source's candidate feed: it is not offered or selected again (step 03)."""


class RequirementNotInStatus(RepositoryError):
    """The requirement's status cannot move to the one asked for (REQUIREMENT_TRANSITIONS)."""


class RequirementAlreadyHasSource(RepositoryError):
    """The requirement's current source choice is already a selection (from 0009: versioned choices)."""


class NothingToRelease(RepositoryError):
    """The requirement's current source choice is not a selection, so there is nothing to release."""


class SourceAlreadyReleased(RepositoryError):
    """This requirement released the source; it cannot select it again (phase 3 plan, assumption 53)."""


class ProposalForAnotherRequirement(RepositoryError):
    """The proposal belongs to another requirement."""


class SourceNotInProposal(RepositoryError):
    """The proposal does not name this source."""


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
