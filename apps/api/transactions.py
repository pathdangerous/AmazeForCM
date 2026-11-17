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
             ArrivalNotChecked, NotAScheduledLoad)
NOT_FOUND = (NotTheRequester, ProposalForAnotherRequirement, ProposalNotFound, FeedNotFound, GlossaryRunNotFound,
             LoadNotFound)
FORBIDDEN = (NotAnApprover, NotTheFeedsOwner)  # the step checks who may decide (403)


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
    except StepInputError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except BaseException:
        session.rollback()
        raise
