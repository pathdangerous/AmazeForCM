"""One step, one transaction: commit it, or roll it back whole, and turn named errors into HTTP answers.

Moved from apps/api/routers/consumer.py (phase 4 plan, assumption 39) so the Amaze router uses the same
helper. Named errors become 409, 404 or 422; anything else (a failed model call, a stub adapter) is
rolled back and raised, never turned into an outcome.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.repositories.errors import (
    AliasAlreadyOnElement,
    ArrivalNotChecked,
    DuplicateFeedCode,
    DuplicateFeedSource,
    NotADropzoneFeed,
    SameApproverTwice,
    UploadNotFound,
    NotAScheduledLoad,
    ElementAlreadyMapped,
    FeedHasNoFile,
    FeedNotACandidate,
    FeedNotFound,
    FeedNotProfiled,
    FeedNotRegistered,
    FeedNotSignedOff,
    GlossaryRunNotFound,
    GoLiveAlreadyProposed,
    LoadInProgress,
    LoadNotFound,
    LoadNotReceived,
    MappingAlreadyProposed,
    MappingChangedMeanwhile,
    NotAnApprover,
    NotLive,
    NotTheFeedsOwner,
    NothingToGoLive,
    NothingToMap,
    OwnerNotADataOwner,
    RulesAlreadyProposed,
    ConfigurationChangedMeanwhile,
    ConfigurationChangedSinceSummary,
    NothingToRelease,
    PhraseTaken,
    ProfileAlreadySummarised,
    ProfileNotSummarised,
    ProfileSuperseded,
    SourceRejected,
    ProposalAlreadyDecided,
    ProposalForAnotherRequirement,
    ProposalNotFound,
    RequirementAlreadyHasSource,
    RequirementNotInStatus,
    SourceAlreadyReleased,
)
from core.repositories.errors import NothingToTransform, SourceFieldNotMapped, TransformationAlreadyProposed
from core.steps.errors import NotTheRequester, StepInputError
from ports.connectors.catalogue import UnknownSource

T = TypeVar("T")

CONFLICTS = (RequirementNotInStatus, ProposalAlreadyDecided, RequirementAlreadyHasSource, NothingToRelease,
             SourceAlreadyReleased, UnknownSource,  # UnknownSource: a listed source left the catalogue
             # phase 4 (plan, assumption 13 and section 9)
             FeedNotACandidate, FeedHasNoFile, FeedNotProfiled, ProfileAlreadySummarised, ProfileSuperseded,
             ProfileNotSummarised, FeedNotSignedOff, FeedNotRegistered, SourceRejected,
             # phase 5 (plan, section 9)
             NothingToMap, MappingAlreadyProposed, ElementAlreadyMapped, PhraseTaken,
             # fixes after the independent review of phase 5 (plan, section 17)
             AliasAlreadyOnElement, MappingChangedMeanwhile,
             # phase 6 (plan, sections 7 and 9)
             RulesAlreadyProposed, ConfigurationChangedMeanwhile, ConfigurationChangedSinceSummary, NothingToGoLive,
             GoLiveAlreadyProposed, OwnerNotADataOwner, LoadInProgress, LoadNotReceived, NotLive,
             # phase 7 (plan, section 9): step 12 comes first
             ArrivalNotChecked, NotAScheduledLoad,
             # phase 7.8, D2: REQUIRE_FOUR_EYES is on and the same person already decided a step
             SameApproverTwice,
             # phase 7.8, task 2.4: the feed code (or its source) an upload names is taken
             DuplicateFeedCode, DuplicateFeedSource,
             # phase 7.8, task 4.4: a re-upload for a feed that reads no drop folder
             NotADropzoneFeed)
NOT_FOUND = (NotTheRequester, ProposalForAnotherRequirement, ProposalNotFound, FeedNotFound, GlossaryRunNotFound,
             LoadNotFound, UploadNotFound)
FORBIDDEN = (NotAnApprover, NotTheFeedsOwner)  # the step checks who may decide (403)
# phase 7.8, task 4.3: step 10 (the transformations); appended here so the tuple above stays as it is
CONFLICTS = CONFLICTS + (NothingToTransform, TransformationAlreadyProposed, SourceFieldNotMapped)
# phase 7.8, task 5.4: the onboarding workspace. DecisionNeeded is not here: the router answers it as a 409 whose body
# lists the items (apps/api/routers/onboarding.py). An edit the kind's payload model or the database refuses is 422.
from core.repositories.errors import (  # noqa: E402 (kept beside its use)
    BackNotAllowed,
    JobSuperseded,
    OnboardingAlreadyExists,
    OnboardingNotFound,
    ProposalEditRefused,
    StepJobActive,
    StepNotEnterable,
    StepNotReady,
)

CONFLICTS = CONFLICTS + (BackNotAllowed, StepNotEnterable, StepJobActive, StepNotReady, OnboardingAlreadyExists,
                         JobSuperseded)
# phase 7.8, task 7.3a: the visibility chosen at go-live is refused once the feed has a product
from core.repositories.errors import FeedHasProduct  # noqa: E402

CONFLICTS = CONFLICTS + (FeedHasProduct,)
NOT_FOUND = NOT_FOUND + (OnboardingNotFound,)
UNPROCESSABLE = (ProposalEditRefused,)


def read(session: Session, work: Callable[[], T]) -> T:
    """Read what a model call needs, then end the transaction (rollback: nothing was written), so the
    connection goes back to the pool before the model is called (review fix 2). Errors as below."""
    return in_transaction(session, work, keep=False)


def in_transaction(session: Session, work: Callable[[], T], *, keep: bool = True) -> T:
    """Run one step and commit it, or roll it back whole. Named errors become 409, 404 or 422; anything
    else (a failed model call, a stub adapter) is rolled back and raised, never turned into an outcome."""
    try:
        result = work()
        if keep:
            session.commit()
        else:
            session.rollback()
        return result
    except CONFLICTS as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except NOT_FOUND:
        session.rollback()
        raise HTTPException(status_code=404, detail="Not found") from None
    except FORBIDDEN as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except UNPROCESSABLE as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except StepInputError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except BaseException:
        session.rollback()
        raise
