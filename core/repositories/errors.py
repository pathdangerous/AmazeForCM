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


class FeedNotLive(RepositoryError):
    """Only a live (registered) feed is paused (phase 7.9, migration 0027)."""


class FeedNotPaused(RepositoryError):
    """Only a paused feed is resumed (phase 7.9, migration 0027)."""


class NotOwnerOrOperations(RepositoryError, PermissionError):
    """Pause, resume and delete are for the feed's registered owner or a user with the operations role (phase 7.9;
    a feed with no owner yet, a candidate, is acted on by operations only)."""


class FeedHasProduct(RepositoryError):
    """A registered feed's visibility is revised only before its product exists (migration 0026): the product
    keeps the visibility of the registration it was created under."""


class UploadNotFound(RepositoryError, LookupError):
    """No uploaded file is stored under this upload id (phase 7.8, task 2.4)."""


class NotADropzoneFeed(RepositoryError):
    """A file was re-uploaded for a feed that does not read a drop folder (phase 7.8, task 4.4)."""


class UploadSourceNotFound(RepositoryError, LookupError):
    """No upload source has this code."""


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


class FeedNotSignedOff(RepositoryError):
    """The feed's latest sign-off does not accept its latest profile, so it cannot be registered (step 04)."""


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


# ---- phase 5: glossary runs, glossary proposals and mappings (all 409 unless said)


class ProposalForAnotherField(RepositoryError):
    """The proposal is for another column of the feed than the configuration being created."""


class NotAProposalOption(RepositoryError):
    """The element, alias or candidate is not one the proposal offers (the step answers 422 first)."""


class PhraseTaken(RepositoryError):
    """Another current glossary element already owns a key, name or alias of the new version."""


class AliasAlreadyOnElement(RepositoryError):
    """The element's current version already has this alias (or it is its key or name): nothing to add."""


class NothingToMap(RepositoryError):
    """The feed's latest profile has no column to look at."""


class MappingAlreadyProposed(RepositoryError):
    """A column of the request got a pending mapping proposal while the agent was answering."""


class MappingChangedMeanwhile(RepositoryError):
    """A column of the request was mapped, or one of its candidates rejected, while the agent was answering."""


class ElementAlreadyMapped(RepositoryError):
    """Another column of the feed is currently mapped to this element."""


class RunSuperseded(RepositoryError):
    """The glossary run is no longer running on this attempt: its lease passed and another worker took it."""


class GlossaryRunNotFound(RepositoryError, LookupError):
    """No glossary run has this id."""


class SourceFieldNotMapped(RepositoryError):
    """The column has no mapping on the feed, so it cannot be transformed (phase 7.8, task 4.1)."""


# ---- loads and products


class LoadNotFound(RepositoryError, LookupError):
    """No load has this id."""


class RulesAlreadyProposed(RepositoryError):
    """While the agent answered, a rule of the same key was proposed, approved or rejected for the feed
    (step 09, phase 6 plan, assumption 11): the answer is discarded."""


class NothingToGoLive(RepositoryError):
    """The feed has no current mapping, so there is nothing to approve for loading (step 11)."""


class GoLiveAlreadyProposed(RepositoryError):
    """The feed already has a go-live summary waiting for its owner (step 11): one at a time."""


class OwnerNotADataOwner(RepositoryError):
    """The owner named at registration no longer holds the data_owner role; nobody can approve this feed's
    go-live until a registration changes that (phase 6 plan, assumption 24)."""


class NotTheFeedsOwner(RepositoryError, PermissionError):
    """Another data owner than the one named in the feed's registration tried to decide its go-live."""


class ConfigurationChangedSinceSummary(RepositoryError):
    """The mappings or rules changed after the summary was written: it cannot be approved, only rejected."""


class NotLive(RepositoryError):
    """The feed has no go-live: it is not loaded until its owner approves one (step 11)."""


class ConfigurationChangedMeanwhile(RepositoryError):
    """While the agent answered, the feed's current mappings changed (steps 09 and 11): the answer is discarded."""


class NothingToTransform(RepositoryError):
    """The feed has no mapped column left that is not already transformed or waiting for a decision (step 10)."""


class TransformationAlreadyProposed(RepositoryError):
    """While the agent answered, a column got a pending transformation proposal: the answer is discarded."""


class LoadNotReceived(RepositoryError):
    """The load is no longer received (another run finished it): the engine writes nothing."""


class ArrivalNotChecked(RepositoryError):
    """A scheduled load is planned for the engine before step 12 passed it (on_time_complete or additive_parked);
    migration 0019, G13, refuses it too."""


class NotAScheduledLoad(RepositoryError):
    """Step 12 checks the arrival of a scheduled load only; a go_live_test load has none (G12)."""


class LoadInProgress(RepositoryError):
    """The feed already has a received load (migration 0018, uq_loads_one_received_per_feed): one at a time."""


class ProductNotFound(RepositoryError, LookupError):
    """No product has this id or code."""


class DuplicateProductCode(RepositoryError):
    """Another product already has this code."""


# ---- consumer requests


class ConsumerRequestNotFound(RepositoryError, LookupError):
    """The requester has no request with this id (it may not exist, or it may be someone else's)."""


# ---- onboarding sessions and proposal edits (phase 7.8, migration 0024)


class OnboardingNotFound(RepositoryError, LookupError):
    """No onboarding session has this id."""


class OnboardingAlreadyExists(RepositoryError):
    """The feed already has an onboarding session (one per feed)."""


class JobSuperseded(RepositoryError):
    """The onboarding job is no longer running on this attempt (its lease was taken again, or it was discarded)."""


class ProposalEditRefused(RepositoryError):
    """The database refused an edit of a proposal (migration 0024): nothing changed, the old proposal is still pending."""


# ---- manual items (phase 7.12, T7; migration 0028)


class ManualItemRefused(RepositoryError):
    """An item a person added is not valid: a column the file lacks, an element that does not exist, a payload the
    kind refuses. Nothing was written. The API answers 422."""


class ManualRuleRefused(ManualItemRefused):
    """A manual rule failed its back-test (step 09's code): not a plain SELECT, an element the feed does not map, an
    error or timeout, or it flags more than half the history. `reason` says which; nothing was written."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ItemAlreadyPending(RepositoryError):
    """The column (or rule key) already has a pending item: edit or reject it first. The API answers 409."""


class ColumnAlreadyMapped(RepositoryError):
    """Only an unmapped column can be excluded: this one has a mapping. The API answers 409."""


# ---- the onboarding workspace (phase 7.8, task 5.3); the API answers each with 409


class DecisionNeeded(RepositoryError):
    """Approve & next is refused: an item of the step needs a person's decision first (accept, edit or reject it).
    `items` are the workspace items that wait."""

    def __init__(self, message: str, items: list | None = None):
        super().__init__(message)
        self.items = items or []


class BackNotAllowed(RepositoryError):
    """Back is refused: the owner approved go-live, the feed is registered (profile and upload stay), or the step is
    not one the session has reached."""


class StepNotEnterable(RepositoryError):
    """Only the current step is entered (again); a finished session enters nothing."""


class StepJobActive(RepositoryError):
    """The session's agent is already working on a step (one queued or running job per session)."""


class StepNotReady(RepositoryError):
    """Approve & next needs a step whose agent has answered: it is waiting, working or failed."""


class UpstreamNotApproved(RepositoryError):
    """Phase 7.13: a draft is approved only after every draft it was built on (top to bottom). The API answers 409."""
