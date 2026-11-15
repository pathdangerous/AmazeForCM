"""The database guards of steps 02 to 04 (phase 4 plan, section 6; assumptions 12, 27, 34; decision D1).

Every guarded row is written with raw SQL, so the repositories' own checks are bypassed and only the
database decides. Each refused statement runs in its own savepoint, so one test can try several rows.
The rows a guard needs to exist (feeds, fetches, proposals, approvals) are written through the
repositories, as the steps would write them.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from tests.conftest import dev_user_for
from tests.onboarded import BACKEND, fetched, registered_feed, rejected_feed, summary

COLUMNS = json.dumps([{"name": "qty", "type": "BIGINT", "null_count": 0, "null_rate": 0.0, "distinct_count": 1}])
SPEC = json.dumps({"source_code": "syn_a", "elements": [], "other_fields": [], "columns": {}})


def refused(session, match, sql, **params):
    with pytest.raises(DBAPIError, match=match):
        with session.begin_nested():
            session.execute(text(sql), params)


class World:
    def __init__(self, session):
        self.s = session
        feeds = FeedRepository(session)
        self.feed, self.other = (
            feeds.create_candidate(code=c, name=c, source_code=c, connector="local_file", source_ref=f"files/{c}.csv").id
            for c in ("syn_a", "syn_b"))

    def next_version(self, table, feed):
        return self.s.scalar(text(f"SELECT COALESCE(max(version), 0) + 1 FROM {table} WHERE feed_id = :f"), {"f": feed})

    PROFILE = ("INSERT INTO feed_profiles (feed_id, version, fetch_id, outcome, row_count, columns, coverage, spec, "
               "detail, engine, profiled_by) VALUES (:feed, :version, :fetch, :outcome, :rows, CAST(:columns AS jsonb), "
               "'[]', CAST(:spec AS jsonb), :detail, 'duckdb 1.5.6', 'dev-backend-team') RETURNING id")

    def profile_params(self, feed=None, *, version=None, fetch=None, outcome="profiled", rows=1, detail=None,
                       columns=COLUMNS):
        feed = feed or self.feed
        return {"feed": feed, "version": self.next_version("feed_profiles", feed) if version is None else version,
                "fetch": fetch or fetched(self.s, feed).id, "outcome": outcome, "rows": rows, "detail": detail,
                "columns": columns, "spec": SPEC}

    def profile(self, feed=None, **over):
        return self.s.execute(text(self.PROFILE), self.profile_params(feed, **over)).scalar_one()

    def spec_only(self, feed=None):
        return self.profile(feed, outcome="spec_only_low_confidence", rows=0, detail="No data rows.", columns="[]")

    def proposal(self, profile_id, feed=None, *, recommended="meets", decision="approved", by="backend_team"):
        proposal = summary(self.s, SimpleNamespace(id=profile_id, feed_id=feed or self.feed), recommended=recommended)
        if decision is not None:
            ProposalRepository(self.s).record_decision(proposal.id, user=dev_user_for(by), decision=decision)
        return proposal.id

    SIGNOFF = ("INSERT INTO feed_signoffs (feed_id, version, profile_id, proposal_id, outcome, gaps, reasons, "
               "signed_off_by) VALUES (:feed, :version, :profile, :proposal, :outcome, :gaps, :reasons, :by) RETURNING id")

    def signoff_params(self, profile, proposal, *, feed=None, version=None, outcome="meets", gaps=(), reasons=(),
                       by=BACKEND):
        feed = feed or self.feed
        return {"feed": feed, "version": self.next_version("feed_signoffs", feed) if version is None else version,
                "profile": profile,
                "proposal": proposal, "outcome": outcome, "gaps": list(gaps), "reasons": list(reasons), "by": by}

    def signoff(self, profile, proposal, **over):
        return self.s.execute(text(self.SIGNOFF), self.signoff_params(profile, proposal, **over)).scalar_one()

    REGISTRATION = ("INSERT INTO feed_registrations (feed_id, version, signoff_id, owner_id, licence, permitted_use, "
                    "redistribution, visibility, registered_by) VALUES (:feed, :version, :signoff, :owner, 'L', 'U', "
                    "'R', :visibility, 'dev-backend-team') RETURNING id")

    def registration_params(self, signoff, *, feed=None, version=None, owner="dev-data-owner", visibility="open"):
        feed = feed or self.feed
        return {"feed": feed,
                "version": self.next_version("feed_registrations", feed) if version is None else version,
                "signoff": signoff, "owner": owner, "visibility": visibility}

    def accepted(self, feed=None, outcome="meets", **over):
        """A profile and an agreeing sign-off of it, all through raw SQL; returns (profile, signoff)."""
        profile = self.profile(feed)
        signoff = self.signoff(profile, self.proposal(profile, feed, recommended=outcome), feed=feed, outcome=outcome,
                               **over)
        return profile, signoff


@pytest.fixture
def world(db_session):
    return World(db_session)


# ---------------------------------------------------------------- feed_profiles


def test_a_profile_version_must_be_the_next_one(world):
    refused(world.s, "is not the next version", World.PROFILE, **world.profile_params(version=2))
    world.profile()
    refused(world.s, "is not the next version", World.PROFILE, **world.profile_params(version=1))
    refused(world.s, "is not the next version", World.PROFILE, **world.profile_params(version=3))
    world.profile(version=2)


def test_a_profile_reads_a_fetched_file_of_its_own_feed(world):
    from core.repositories.source_fetches import SourceFetchRepository

    down = SourceFetchRepository(world.s).record(feed_id=world.feed, outcome="source_down", detail="no answer").id
    refused(world.s, "a fetched file of its own feed", World.PROFILE, **world.profile_params(fetch=down))
    refused(world.s, "a fetched file of its own feed", World.PROFILE,
            **world.profile_params(fetch=fetched(world.s, world.other).id))


@pytest.mark.parametrize(("over", "constraint"), [
    ({"rows": 0}, "ck_feed_profiles_profiled_has_rows"),
    ({"rows": None}, "ck_feed_profiles_profiled_has_rows"),
    ({"detail": "why"}, "ck_feed_profiles_profiled_has_rows"),
    ({"columns": "[]"}, "ck_feed_profiles_profiled_has_rows"),
    ({"outcome": "spec_only_low_confidence", "rows": 0, "detail": None}, "ck_feed_profiles_spec_only_has_a_reason"),
    ({"outcome": "spec_only_low_confidence", "rows": 0, "detail": " "}, "ck_feed_profiles_spec_only_has_a_reason"),
    ({"outcome": "spec_only_low_confidence", "rows": 4, "detail": "why"}, "ck_feed_profiles_spec_only_has_a_reason"),
])
def test_null_never_passes_a_profile_check(world, over, constraint):
    refused(world.s, constraint, World.PROFILE, **world.profile_params(**over))


PROFILE_RAW = ("INSERT INTO feed_profiles (feed_id, version, fetch_id, outcome, row_count, columns, coverage, spec, "
               "detail, engine, profiled_by) VALUES (:feed, :version, :fetch, :outcome, 1, CAST(:columns AS jsonb), "
               "CAST(:coverage AS jsonb), CAST(:spec AS jsonb), NULL, :engine, :by)")


@pytest.mark.parametrize(("over", "constraint"), [
    ({"outcome": "guessed"}, "ck_feed_profiles_outcome"),
    ({"coverage": "{}"}, "ck_feed_profiles_json_shapes"),
    ({"spec": "[]"}, "ck_feed_profiles_json_shapes"),
    ({"engine": " "}, "ck_feed_profiles_text_present"),
    ({"by": ""}, "ck_feed_profiles_text_present"),
])
def test_the_other_profile_checks(world, over, constraint):
    params = {"feed": world.feed, "version": 1, "fetch": fetched(world.s, world.feed).id, "outcome": "profiled",
              "columns": COLUMNS, "coverage": "[]", "spec": SPEC, "engine": "duckdb 1.5.6", "by": BACKEND, **over}
    refused(world.s, constraint, PROFILE_RAW, **params)


@pytest.mark.parametrize("column", ["owner_id", "licence", "permitted_use", "redistribution", "registered_by"])
def test_a_registration_needs_every_text(world, column):
    _, signoff = world.accepted()
    values = {"owner_id": "'dev-data-owner'", "licence": "'L'", "permitted_use": "'U'", "redistribution": "'R'",
              "registered_by": "'dev-backend-team'", column: "'  '"}
    refused(world.s, "ck_feed_registrations_text_present",
            "INSERT INTO feed_registrations (feed_id, version, signoff_id, owner_id, licence, permitted_use, "
            "redistribution, visibility, registered_by) VALUES (:feed, 1, :signoff, "
            f"{values['owner_id']}, {values['licence']}, {values['permitted_use']}, {values['redistribution']}, "
            f"'open', {values['registered_by']})", feed=world.feed, signoff=signoff)


def test_version_zero_is_refused_by_the_version_trigger_before_the_check(world):
    """ck_<table>_version (version >= 1) stands behind trg_<table>_a_version, which answers first: a BEFORE
    trigger runs before the check constraints."""
    refused(world.s, "feed_profiles version 0 is not the next version", World.PROFILE,
            **world.profile_params(version=0))
    profile = world.profile()
    proposal = world.proposal(profile)
    refused(world.s, "feed_signoffs version 0 is not the next version", World.SIGNOFF,
            **world.signoff_params(profile, proposal, version=0))
    signoff = world.signoff(profile, proposal)
    refused(world.s, "feed_registrations version 0 is not the next version", World.REGISTRATION,
            **world.registration_params(signoff, version=0))


def test_a_signoffs_outcome_and_signer_are_refused_by_its_triggers_before_the_checks(world):
    """ck_feed_signoffs_outcome and ck_feed_signoffs_signed_off_by stand behind b_guard and c_decision."""
    profile = world.profile()
    proposal = world.proposal(profile)
    refused(world.s, "agreed with its recommended outcome", World.SIGNOFF,
            **world.signoff_params(profile, proposal, outcome="maybe"))
    refused(world.s, "the person who decided", World.SIGNOFF, **world.signoff_params(profile, proposal, by=" "))


def test_a_spec_only_profile_may_have_no_row_count(world):
    world.profile(outcome="spec_only_low_confidence", rows=None, detail="Not a table.", columns="[]")


# ---------------------------------------------------------------- feed_signoffs


def test_a_signoff_version_must_be_the_next_one(world):
    profile = world.profile()
    proposal = world.proposal(profile)
    refused(world.s, "is not the next version", World.SIGNOFF, **world.signoff_params(profile, proposal, version=2))


def test_only_a_candidate_feed_is_signed_off(world):
    registered_feed(world.s, world.other)
    profile = world.profile(world.other)
    refused(world.s, "only a candidate feed is signed off", World.SIGNOFF,
            **world.signoff_params(profile, world.proposal(profile, world.other), feed=world.other))


def test_a_signoff_is_of_the_feeds_latest_profile(world):
    older = world.profile()
    proposal = world.proposal(older)
    world.profile()
    refused(world.s, "is not the latest profile", World.SIGNOFF, **world.signoff_params(older, proposal))
    elsewhere = world.profile(world.other)
    refused(world.s, "is not the latest profile", World.SIGNOFF,
            **world.signoff_params(elsewhere, world.proposal(elsewhere, world.other)))


def test_the_proposal_is_the_profile_summary_of_that_profile(world, make_proposal):
    first = world.profile()
    of_first = world.proposal(first)
    second = world.profile()
    refused(world.s, "is not the profile_summary proposal of profile", World.SIGNOFF,
            **world.signoff_params(second, of_first))
    mapping = make_proposal(kind="mapping", roles=("backend_team",), feed_id=world.feed,
                            payload={"profile_id": str(second), "recommended_outcome": "meets"})
    ProposalRepository(world.s).record_decision(mapping.id, user=dev_user_for("backend_team"), decision="approved")
    refused(world.s, "is not the profile_summary proposal of profile", World.SIGNOFF,
            **world.signoff_params(second, mapping.id))


def test_the_signer_is_the_person_who_decided_the_summary(world):
    profile = world.profile()
    proposal = world.proposal(profile)
    refused(world.s, "the person who decided", World.SIGNOFF,
            **world.signoff_params(profile, proposal, by="dev-data-steward"))
    world.signoff(profile, proposal)


def test_a_spec_only_profile_cannot_meet(world):
    profile = world.spec_only()
    refused(world.s, "spec-only profile cannot meet", World.SIGNOFF,
            **world.signoff_params(profile, world.proposal(profile)))
    world.signoff(profile, world.proposal(profile, recommended="rejected"), outcome="rejected", reasons=["No data"])


def test_an_approved_summary_needs_the_signoff_to_hold_the_recommended_outcome(world):
    """D1: approving the summary means the person agrees with its recommended outcome."""
    profile = world.profile()
    proposal = world.proposal(profile, recommended="meets_with_gaps", decision="approved")
    refused(world.s, "agreed with its recommended outcome", World.SIGNOFF,
            **world.signoff_params(profile, proposal, outcome="meets"))
    refused(world.s, "agreed with its recommended outcome", World.SIGNOFF,
            **world.signoff_params(profile, proposal, outcome="rejected", reasons=["r"]))
    world.signoff(profile, proposal, outcome="meets_with_gaps", gaps=["g"])


def test_a_rejected_summary_needs_the_signoff_to_hold_another_outcome(world):
    """D1: rejecting the summary means the person chose another outcome than the recommended one."""
    profile = world.profile()
    proposal = world.proposal(profile, recommended="meets_with_gaps", decision="rejected")
    refused(world.s, "chose another outcome", World.SIGNOFF,
            **world.signoff_params(profile, proposal, outcome="meets_with_gaps", gaps=["g"]))
    world.signoff(profile, proposal, outcome="meets")


def test_a_pending_summary_has_no_signer(world):
    """b_guard: nobody decided a pending summary, so no one can be the signer."""
    profile = world.profile()
    refused(world.s, "the sign-off must name the person who decided proposal .* as backend_team, not dev-backend-team",
            World.SIGNOFF, **world.signoff_params(profile, world.proposal(profile, decision=None)))


def test_a_pending_summary_is_refused_by_the_decision_guard(world):
    """c_decision on its own: an approvals row by the signer exists (so b_guard passes) while the proposal
    stays pending, which only raw SQL can make."""
    profile = world.profile()
    proposal = world.proposal(profile, decision=None)
    world.s.execute(text("INSERT INTO approvals (proposal_id, step, role, decided_by, decision) "
                         "VALUES (:p, 1, 'backend_team', :by, 'approved')"), {"p": proposal, "by": BACKEND})
    refused(world.s, "proposal .* is pending: a sign-off needs a decided summary", World.SIGNOFF,
            **world.signoff_params(profile, proposal))


def test_a_summary_without_a_recommendation_cannot_be_signed_off(world, make_proposal):
    profile = world.profile()
    bare = make_proposal(kind="profile_summary", roles=("backend_team",), feed_id=world.feed,
                         payload={"profile_id": str(profile)})
    ProposalRepository(world.s).record_decision(bare.id, user=dev_user_for("backend_team"), decision="approved")
    refused(world.s, "names no recommended_outcome", World.SIGNOFF, **world.signoff_params(profile, bare.id))


@pytest.mark.parametrize(("outcome", "gaps", "reasons"), [
    ("meets", ["g"], []), ("meets_with_gaps", [], []), ("rejected", [], []), ("rejected", ["g"], ["r"]),
])
def test_gaps_and_reasons_fit_the_outcome(world, outcome, gaps, reasons):
    profile = world.profile()
    proposal = world.proposal(profile, recommended=outcome)
    refused(world.s, "ck_feed_signoffs_gaps_and_reasons_fit", World.SIGNOFF,
            **world.signoff_params(profile, proposal, outcome=outcome, gaps=gaps, reasons=reasons))


# ---------------------------------------------------------------- feed_registrations


def test_a_registration_needs_the_latest_accepting_signoff_on_the_latest_profile(world):
    _, older = world.accepted()
    _, latest = world.accepted(outcome="meets_with_gaps", gaps=["g"])
    refused(world.s, "is not the latest sign-off", World.REGISTRATION, **world.registration_params(older))
    world.profile()  # a newer profile, not yet signed off
    refused(world.s, "a newer profile waits for a sign-off", World.REGISTRATION, **world.registration_params(latest))


def test_a_rejecting_signoff_does_not_register(world):
    _, rejecting = world.accepted(outcome="rejected", reasons=["No data"])
    refused(world.s, "does not accept the feed", World.REGISTRATION, **world.registration_params(rejecting))


def test_a_registered_feed_gets_no_second_registration(world):
    registered_feed(world.s, world.feed)
    signoff = world.s.scalar(text("SELECT id FROM feed_signoffs WHERE feed_id = :f ORDER BY version DESC LIMIT 1"),
                             {"f": world.feed})
    refused(world.s, "feed .* is registered: only a candidate feed is registered", World.REGISTRATION,
            **world.registration_params(signoff, owner="dev-checker"))


def test_a_registration_version_and_visibility(world):
    _, signoff = world.accepted()
    refused(world.s, "is not the next version", World.REGISTRATION, **world.registration_params(signoff, version=2))
    refused(world.s, "ck_feed_registrations_visibility", World.REGISTRATION,
            **world.registration_params(signoff, visibility="everyone"))
    world.s.execute(text(World.REGISTRATION), world.registration_params(signoff))


# ---------------------------------------------------------------- feeds.stage


def test_a_new_feed_is_a_candidate(world):
    refused(world.s, "a new feed is a candidate", "INSERT INTO feeds (code, name, stage, owner_id, registered_at) "
                                                  "VALUES ('syn_x', 'X', 'registered', 'dev-data-owner', now())")
    refused(world.s, "a new feed is a candidate", "INSERT INTO feeds (code, name, stage) VALUES ('syn_y', 'Y', 'rejected')")


REGISTER = "UPDATE feeds SET stage = 'registered', owner_id = :owner, registered_at = now() WHERE id = :id"
REJECT = "UPDATE feeds SET stage = 'rejected' WHERE id = :id"


def test_registered_needs_a_registration_of_the_same_owner(world):
    refused(world.s, "needs a registration of owner", REGISTER, id=world.feed, owner="dev-data-owner")
    _, signoff = world.accepted()
    world.s.execute(text(World.REGISTRATION), world.registration_params(signoff))
    refused(world.s, "needs a registration of owner", REGISTER, id=world.feed, owner="dev-checker")
    world.s.execute(text(REGISTER), {"id": world.feed, "owner": "dev-data-owner"})


def test_rejected_needs_a_rejected_signoff(world):
    refused(world.s, "needs a rejected sign-off", REJECT, id=world.feed)
    world.accepted()
    refused(world.s, "needs a rejected sign-off", REJECT, id=world.feed)
    world.accepted(outcome="rejected", reasons=["No data"])
    world.s.execute(text(REJECT), {"id": world.feed})


def test_no_other_change_of_stage_and_a_registered_owner_is_fixed(world):
    registered_feed(world.s, world.feed)
    rejected_feed(world.s, world.other)
    back = "UPDATE feeds SET stage = 'candidate', registered_at = NULL WHERE id = :id"
    refused(world.s, "cannot move from registered to candidate", back, id=world.feed)
    refused(world.s, "cannot move from rejected to candidate", "UPDATE feeds SET stage = 'candidate' WHERE id = :id",
            id=world.other)
    refused(world.s, "cannot move from registered to rejected",
            "UPDATE feeds SET stage = 'rejected', registered_at = NULL WHERE id = :id", id=world.feed)
    refused(world.s, "the owner of a registered feed is fixed", "UPDATE feeds SET owner_id = 'dev-checker' WHERE id = :id",
            id=world.feed)


def test_a_candidates_owner_may_still_be_set(world):
    world.s.execute(text("UPDATE feeds SET owner_id = 'dev-data-owner' WHERE id = :id"), {"id": world.feed})


# ---------------------------------------------------------------- source_choices and a rejected feed

CHOICE = ("INSERT INTO source_choices (requirement_id, version, action, feed_id, source_code, basis, reason) "
          "VALUES (:requirement, :version, :action, :feed, :source, :basis, :reason)")


def test_a_rejected_feed_cannot_be_selected(world):
    rejected_feed(world.s, world.other)
    requirement = RequirementRepository(world.s).create(requester_id="dev-requester", title="t").id
    refused(world.s, "is rejected: it cannot be selected", CHOICE, requirement=requirement, version=1,
            action="selected", feed=world.other, source="syn_b", basis="known_source", reason=None)


def test_a_candidate_rejected_release_needs_a_rejected_feed(world):
    requirement = RequirementRepository(world.s).create(requester_id="dev-requester", title="t").id
    SourceChoiceRepository(world.s).record_known(requirement_id=requirement, feed_id=world.feed, source_code="syn_a")
    release = {"requirement": requirement, "version": 2, "action": "released", "feed": world.feed, "source": "syn_a",
               "basis": None, "reason": "candidate_rejected"}
    refused(world.s, "a candidate_rejected release needs a rejected feed", CHOICE, **release)
    rejected_feed(world.s, world.feed)
    world.s.execute(text(CHOICE), release)


def test_the_reason_list_has_the_third_reason_only(world):
    requirement = RequirementRepository(world.s).create(requester_id="dev-requester", title="t").id
    SourceChoiceRepository(world.s).record_known(requirement_id=requirement, feed_id=world.feed, source_code="syn_a")
    refused(world.s, "ck_source_choices_reason", CHOICE, requirement=requirement, version=2, action="released",
            feed=world.feed, source="syn_a", basis=None, reason="backend_said_no")


def test_the_onboarded_helpers_pass_every_guard(world):
    assert registered_feed(world.s, world.feed).stage == "registered"
    assert rejected_feed(world.s, world.other).stage == "rejected"
