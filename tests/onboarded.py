"""Walk the real onboarding chain through the repositories (phase 4 plan, assumption 35).

From migration 0014 the database refuses a registered feed without a registration row, and a rejected
feed without a rejected sign-off. Tests that need either use these helpers, which write every row the
guards ask for: a fetched file, a profile, a decided profile_summary proposal, a sign-off and, for a
registration, its row. No trigger is bypassed. Each helper flushes; the test's session rolls back.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel

from core.domain.feed import Feed
from core.domain.profiling import FeedProfile, FeedSignoff, FileColumn, SourceSpec
from core.proposal_log import ProposalLog
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feed_signoffs import FeedSignoffRepository
from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.schedules import FeedScheduleRepository
from core.repositories.source_fetches import SourceFetchRepository
from tests.conftest import dev_user_for

BACKEND = "dev-backend-team"
SPEC = SourceSpec(source_code="syn_test", elements=["volume"], other_fields=[], columns={"qty": "volume"})
COLUMN = FileColumn(name="qty", type="BIGINT", null_count=0, null_rate=0.0, distinct_count=1)


class _Summary(BaseModel):
    recommended_outcome: str


def fetched(session, feed_id: UUID):
    return SourceFetchRepository(session).record(feed_id=feed_id, outcome="fetched",
                                                 blob_ref=f"raw/test/{feed_id}/f.csv", size_bytes=10)


def profiled(session, feed_id: UUID, *, outcome: str = "profiled", spec: SourceSpec = SPEC,
             columns: Sequence[FileColumn] = (COLUMN,)) -> FeedProfile:
    """spec and columns: what the profile says (phase 5: the columns steps 05 and 06 look at)."""
    fields = ({"row_count": 1, "columns": list(columns)} if outcome == "profiled"
              else {"row_count": 0, "columns": [], "detail": "The file has a header and no data rows."})
    return FeedProfileRepository(session).add_version(
        feed_id=feed_id, fetch_id=fetched(session, feed_id).id, outcome=outcome, coverage=[], spec=spec,
        engine="duckdb 1.5.6", profiled_by=BACKEND, **fields)


def summary(session, profile: FeedProfile, *, recommended: str = "meets"):
    """A pending profile_summary proposal of the profile, on a logged call (as the ProfilerAgent's)."""
    from ports.llm.base import LLMResult

    result = LLMResult(output=_Summary(recommended_outcome=recommended), provider="test", model="test",
                       model_version="0", prompt_id="profiler", prompt_version="v1", latency_ms=0, raw_ref=None)
    entry = ProposalLog(session).record(agent_name="ProfilerAgent", input_ref="test://profile", result=result)
    return ProposalRepository(session).create(
        proposal_log_id=entry.id, kind="profile_summary", autonomy="A2", required_approver_roles=["backend_team"],
        feed_id=profile.feed_id, payload={"profile_id": str(profile.id), "summary": "Synthetic summary.",
                                          "recommended_outcome": recommended, "gaps": [], "reasons": []})


def signed_off(session, feed_id: UUID, *, outcome: str = "meets", gaps: Sequence[str] = (),
               reasons: Sequence[str] = (), **profile) -> FeedSignoff:
    """A profile, its summary recommending this outcome, the backend team agreeing (D1), the sign-off."""
    profile = profiled(session, feed_id, **profile)
    proposal = summary(session, profile, recommended=outcome)
    ProposalRepository(session).record_decision(proposal.id, user=dev_user_for("backend_team"), decision="approved")
    return FeedSignoffRepository(session).add_version(
        feed_id=feed_id, profile_id=profile.id, proposal_id=proposal.id, outcome=outcome, gaps=list(gaps),
        reasons=list(reasons), signed_off_by=BACKEND)


def registration(session, feed_id: UUID, *, owner_id: str = "dev-data-owner", visibility: str = "open", **profile):
    signoff = signed_off(session, feed_id, **profile)
    return FeedRegistrationRepository(session).add_version(
        feed_id=feed_id, signoff_id=signoff.id, owner_id=owner_id, licence="Synthetic licence",
        permitted_use="Internal analysis", redistribution="Not allowed", visibility=visibility, registered_by=BACKEND)


def registered_feed(session, feed_id: UUID, *, owner_id: str = "dev-data-owner", scheduled: bool = True,
                    **profile) -> Feed:
    """profile: spec= and columns= for the signed-off profile (default: one column qty, declared volume).
    scheduled (phase 7.21): the feed also gets a schedule, version 1 (daily 06:30 UTC, 120 minutes grace), as the upload
    screen's question gives every new feed; False leaves it "Schedule not set", as a feed made before 7.21 is."""
    registration(session, feed_id, owner_id=owner_id, **profile)
    feed = FeedRepository(session).register(feed_id, owner_id=owner_id)
    if scheduled:
        from tests.schedules import DEFAULT_INPUT

        FeedScheduleRepository(session).add_version(feed_id=feed_id, schedule=DEFAULT_INPUT, decided_by=BACKEND)
    return feed


def rejected_feed(session, feed_id: UUID, *, reasons: Sequence[str] = ("Synthetic reason",)) -> Feed:
    signed_off(session, feed_id, outcome="rejected", reasons=reasons)
    return FeedRepository(session).reject(feed_id)
