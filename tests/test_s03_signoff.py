"""Step 03 sign-off (phase 4 plan, assumptions 18 to 27; decision D1).

Outcomes (CLAUDE.md): meets; meets_with_gaps; rejected. The backend team decides the ProfilerAgent's
summary of the feed's latest profile: approving it means agreeing with its recommended outcome,
rejecting it means choosing another (D1). feed_signoffs keeps the person's outcome, gaps and reasons.
A rejection rejects the feed and releases every requirement that selects it now (candidate_rejected);
each requester is told the reasons.
"""

from __future__ import annotations

import pytest

from core.repositories.errors import (
    FeedNotACandidate,
    FeedNotProfiled,
    ProfileNotSummarised,
    ProposalAlreadyDecided,
)
from core.repositories.feed_signoffs import FeedSignoffRepository
from core.repositories.feeds import FeedRepository
from core.repositories.notifications import NotificationRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.steps.known_source import find_known_source
from core.steps.requirement_page import ONBOARDING, requirement_page
from core.steps.s03_signoff import Meets, MeetsWithGaps, Rejected, SignoffDoesNotFit, sign_off
from core.steps.s3_pick import pick_source
from ports.notify.inapp import InAppNotifier
from tests.conftest import dev_user_for
from tests.onboarded import profiled, summary
from tests.test_s3_pick import BACKUP, CATALOGUE, VENDOR, conditions, listed  # noqa: F401 (fixture)

BACKEND = dev_user_for("backend_team")
TEAM = ["dev-backend-team"]


def onboarding_on(db_session, req, proposal_id, code):
    """The requester picks the source and its first file arrives: the requirement is onboarding."""
    pick_source(db_session, requirement_id=req.id, user=dev_user_for("requester"), proposal_id=proposal_id,
                accepted_conditions=conditions(code), catalogue=CATALOGUE)
    RequirementRepository(db_session).advance(req.id, "onboarding")
    return FeedRepository(db_session).get_by_source(code)


@pytest.fixture
def vendor(db_session, listed):
    """open price, onboarding on VENDOR, with BACKUP still listed; the feed profiled and summarised."""
    req, proposals = listed()
    feed = onboarding_on(db_session, req, proposals[VENDOR], VENDOR)
    return req, feed


def summarised(db_session, feed, recommended="meets", outcome="profiled"):
    profile = profiled(db_session, feed.id, outcome=outcome)
    return profile, summary(db_session, profile, recommended=recommended)


def decide(db_session, feed, outcome, gaps=(), reasons=(), user=BACKEND):
    return sign_off(db_session, feed_id=feed.id, user=user, outcome=outcome, gaps=list(gaps), reasons=list(reasons),
                    catalogue=CATALOGUE, notifier=InAppNotifier(db_session), backend_team=TEAM)


def riley(db_session):
    return [(n.subject, n.body) for n in NotificationRepository(db_session).list_for("dev-requester")]


def status(db_session, req):
    return RequirementRepository(db_session).get(req.id).status


def test_meets_keeps_the_candidate_and_shows_the_requester_nothing_new(db_session, vendor):
    req, feed = vendor
    profile, proposal = summarised(db_session, feed, "meets")
    before = riley(db_session)
    result = decide(db_session, feed, "meets")
    assert isinstance(result, Meets)
    assert (result.signoff.version, result.signoff.profile_id, result.signoff.outcome, result.signoff.signed_off_by) == (
        1, profile.id, "meets", "dev-backend-team")
    assert FeedRepository(db_session).get(feed.id).stage == "candidate"
    assert status(db_session, req) == "onboarding"
    assert riley(db_session) == before
    page = requirement_page(db_session, RequirementRepository(db_session).get(req.id), CATALOGUE)
    assert (page.message, page.signoff_note) == (ONBOARDING, None)


def test_meets_with_gaps_keeps_the_gaps_and_tells_the_requester(db_session, vendor):
    req, feed = vendor
    summarised(db_session, feed, "meets_with_gaps")
    gaps = ["open_price is empty in 2 of 10 rows", "No prices before 2026-11-02"]
    result = decide(db_session, feed, "meets_with_gaps", gaps=gaps)
    assert isinstance(result, MeetsWithGaps)
    assert (result.signoff.gaps, result.told) == (gaps, [req.id])
    assert FeedSignoffRepository(db_session).latest(feed.id).gaps == gaps
    note = ("The backend team accepted Vendor end-of-day prices with gaps: open_price is empty in 2 of 10 rows; "
            "No prices before 2026-11-02.")
    assert riley(db_session)[0] == ("Accepted with gaps: Vendor end-of-day prices", note)
    page = requirement_page(db_session, RequirementRepository(db_session).get(req.id), CATALOGUE)
    assert (page.status, page.message, page.signoff_note) == ("onboarding", ONBOARDING, note)


def test_rejected_keeps_the_reasons_releases_the_requirement_and_tells_the_requester(db_session, vendor):
    req, feed = vendor
    summarised(db_session, feed, "meets_with_gaps")
    result = decide(db_session, feed, "rejected", reasons=["Prices are a day late", "No vendor agreement on file"])
    assert isinstance(result, Rejected)
    assert result.signoff.reasons == ["Prices are a day late", "No vendor agreement on file"]
    assert FeedRepository(db_session).get(feed.id).stage == "rejected"
    [released] = result.released
    assert (released.requirement_id, released.reason, released.status) == (req.id, "candidate_rejected",
                                                                           "awaiting_source_choice")
    current = SourceChoiceRepository(db_session).current(req.id)
    assert (current.action, current.reason, current.decided_by) == ("released", "candidate_rejected", None)
    message = ("The backend team rejected Vendor end-of-day prices: Prices are a day late; No vendor agreement on "
               "file. Pick another source.")
    assert riley(db_session)[0] == ("Source rejected: Vendor end-of-day prices", message)
    page = requirement_page(db_session, RequirementRepository(db_session).get(req.id), CATALOGUE)
    assert (page.status, page.message) == ("awaiting_source_choice", message)
    assert [c.source.code for c in page.listed] == [BACKUP]
    team = [n.subject for n in NotificationRepository(db_session).list_for("dev-backend-team")]
    assert f"Source released: {VENDOR}" in team


def test_a_rejection_goes_to_awaiting_source_choice_when_a_listed_source_is_left(db_session, vendor):
    req, feed = vendor
    summarised(db_session, feed, "rejected")
    decide(db_session, feed, "rejected", reasons=["No data to check"])
    assert status(db_session, req) == "awaiting_source_choice"


def test_a_rejection_goes_to_no_source_found_when_none_is_left(db_session, glossary):
    req = RequirementRepository(db_session).create(requester_id="dev-requester", title="volume",
                                                   element_keys=["volume"])
    feed = find_known_source(db_session, requirement_id=req.id, catalogue=CATALOGUE).feed
    RequirementRepository(db_session).advance(req.id, "onboarding")
    summarised(db_session, feed, "rejected")
    decide(db_session, feed, "rejected", reasons=["Volumes are rounded"])
    assert status(db_session, req) == "no_source_found"
    message = ("The backend team rejected Exchange daily volumes: Volumes are rounded. No other source is listed. "
               "Add a note and ask for a new scope.")
    page = requirement_page(db_session, RequirementRepository(db_session).get(req.id), CATALOGUE)
    assert page.message == message
    assert riley(db_session)[0][1] == message


def test_only_requirements_that_select_the_feed_now_are_told_and_released(db_session, glossary):
    """Note 4b: feeds.requirement_id keeps the first requirement, even after it released the feed."""
    from core.steps.release import release_source

    repo = RequirementRepository(db_session)
    first = repo.create(requester_id="dev-requester", title="volume", element_keys=["volume"])
    feed = find_known_source(db_session, requirement_id=first.id, catalogue=CATALOGUE).feed
    release_source(db_session, requirement_id=first.id, reason="requester_asked", user=dev_user_for("requester"),
                   catalogue=CATALOGUE, notifier=InAppNotifier(db_session), backend_team=TEAM)
    second = repo.create(requester_id="dev-requester", title="volume again", element_keys=["volume"])
    find_known_source(db_session, requirement_id=second.id, catalogue=CATALOGUE)
    assert FeedRepository(db_session).get(feed.id).requirement_id == first.id
    summarised(db_session, feed, "rejected")
    result = decide(db_session, feed, "rejected", reasons=["Volumes are rounded"])
    assert [r.requirement_id for r in result.released] == [second.id]
    assert (status(db_session, first), status(db_session, second)) == ("no_source_found", "no_source_found")
    assert SourceChoiceRepository(db_session).current(first.id).reason == "requester_asked"


def test_agreeing_with_the_recommendation_approves_the_summary(db_session, vendor):
    _, feed = vendor
    _, proposal = summarised(db_session, feed, "meets_with_gaps")
    decide(db_session, feed, "meets_with_gaps", gaps=["g"])
    [approval] = ProposalRepository(db_session).approvals(proposal.id)
    assert (ProposalRepository(db_session).get(proposal.id).status, approval.decision, approval.role,
            approval.decided_by) == ("approved", "approved", "backend_team", "dev-backend-team")


def test_choosing_another_outcome_rejects_the_summary(db_session, vendor):
    _, feed = vendor
    _, proposal = summarised(db_session, feed, "meets_with_gaps")
    result = decide(db_session, feed, "meets")
    assert result.signoff.outcome == "meets"
    assert ProposalRepository(db_session).get(proposal.id).status == "rejected"
    assert FeedSignoffRepository(db_session).latest(feed.id).outcome == "meets"


def test_meets_is_refused_on_a_spec_only_profile(db_session, vendor):
    _, feed = vendor
    summarised(db_session, feed, "meets_with_gaps", outcome="spec_only_low_confidence")
    with pytest.raises(SignoffDoesNotFit, match="spec-only"):
        decide(db_session, feed, "meets")
    assert FeedSignoffRepository(db_session).latest(feed.id) is None


@pytest.mark.parametrize(("outcome", "gaps", "reasons"), [
    ("meets", ["g"], []), ("meets", [], ["r"]), ("meets_with_gaps", [], []), ("meets_with_gaps", ["g"], ["r"]),
    ("rejected", [], []), ("rejected", ["g"], ["r"]), ("rejected", [], ["  "]),
])
def test_gaps_and_reasons_must_fit_the_outcome(db_session, vendor, outcome, gaps, reasons):
    _, feed = vendor
    summarised(db_session, feed)
    with pytest.raises(SignoffDoesNotFit):
        decide(db_session, feed, outcome, gaps=gaps, reasons=reasons)


def test_a_signoff_needs_a_summarised_latest_profile_of_a_candidate(db_session, vendor):
    _, feed = vendor
    with pytest.raises(FeedNotProfiled):
        decide(db_session, feed, "meets")
    summarised(db_session, feed)
    profiled(db_session, feed.id)  # a newer profile, not summarised yet
    with pytest.raises(ProfileNotSummarised):
        decide(db_session, feed, "meets")


def test_a_summary_is_decided_once(db_session, vendor):
    _, feed = vendor
    summarised(db_session, feed)
    decide(db_session, feed, "meets")
    with pytest.raises(ProposalAlreadyDecided):
        decide(db_session, feed, "meets")


def test_a_rejected_feed_is_not_signed_off_again(db_session, vendor):
    _, feed = vendor
    summarised(db_session, feed, "rejected")
    decide(db_session, feed, "rejected", reasons=["r"])
    with pytest.raises(FeedNotACandidate):
        decide(db_session, feed, "meets")
