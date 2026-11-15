"""S3 pick and decline (core/steps/s3_pick.py; phase 3 plan, assumptions 33 to 37 and 56).

Outcomes (CLAUDE.md): source_picked; approval_missing. Option A: the requester's pick, with the source's
conditions in force accepted, is the only approval. The acceptance names the conditions; missing or
different from the catalogue's now gives approval_missing, and nothing is written or fetched.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from core.repositories.errors import ProposalAlreadyDecided, ProposalForAnotherRequirement, RequirementNotInStatus
from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirement_scopes import RequirementScopeRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.tables import ApprovalRow, FeedRow, SourceChoiceRow, SourceFetchRow
from core.steps.errors import NotTheRequester
from core.steps.s3_pick import ApprovalMissing, SourcePicked, decline_source, pick_source
from tests.step_chains import list_sources
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from ports.identity.base import User
from ports.notify.inapp import InAppNotifier
from tests.conftest import dev_user_for
from tests.test_s2_sources import PUBLIC, ranking_agent

CATALOGUE = SourceCatalogue.load(DEFAULT_CATALOGUE)
REQUESTER = dev_user_for("requester")
VENDOR, BACKUP = "syn_vendor_eod_prices", "syn_backup_eod_prices"


@pytest.fixture
def listed(db_session, glossary, make_proposal):
    """A requirement for open price with VENDOR and BACKUP listed (awaiting_source_choice).
    Returns (requirement, {source code: proposal id})."""
    def _make(title="open price"):
        req = RequirementRepository(db_session).create(requester_id="dev-requester", title=title,
                                                       element_keys=["open_price"])
        proposal = make_proposal(kind="scope", roles=("requester",), payload=PUBLIC, requirement_id=req.id)
        ProposalRepository(db_session).record_decision(proposal.id, user=REQUESTER, decision="approved")
        RequirementScopeRepository(db_session).add_version(
            requirement_id=req.id, proposal_id=proposal.id, confirmed_by="dev-requester",
            visibility="public", provider=PUBLIC["provider"], where_to_search=PUBLIC["where_to_search"])
        db_session.execute(text("UPDATE requirements SET status = 'scope_confirmed' WHERE id = :id"), {"id": req.id})
        result = list_sources(db_session, requirement_id=req.id, agent=ranking_agent(), catalogue=CATALOGUE,
                              notifier=InAppNotifier(db_session), backend_team=["dev-backend-team"])
        return req, {c.source.code: c.proposal_id for c in result.candidates}

    return _make


def pick(db_session, req, proposal_id, accepted, catalogue=CATALOGUE, user=REQUESTER):
    return pick_source(db_session, requirement_id=req.id, user=user, proposal_id=proposal_id,
                       accepted_conditions=accepted, catalogue=catalogue)


def conditions(code):
    return CATALOGUE.get(code).conditions


def count(session, table):
    return session.scalar(select(func.count()).select_from(table))


def status(session, req):
    return RequirementRepository(session).get(req.id).status


def snapshot(session):
    return tuple(count(session, t) for t in (ApprovalRow, FeedRow, SourceChoiceRow, SourceFetchRow))


def test_source_picked_when_the_requester_accepts_the_conditions(db_session, listed):
    req, proposals = listed()
    result = pick(db_session, req, proposals[VENDOR], conditions(VENDOR))
    assert isinstance(result, SourcePicked)
    assert (result.source.code, result.feed_code) == (VENDOR, VENDOR)
    proposal = ProposalRepository(db_session).get(proposals[VENDOR])
    assert proposal.status == "approved"
    [approval] = ProposalRepository(db_session).approvals(proposal.id)
    assert (approval.role, approval.decided_by, approval.decision) == ("requester", "dev-requester", "approved")
    feed = FeedRepository(db_session).get_by_source(VENDOR)
    assert (feed.stage, feed.requirement_id) == ("candidate", req.id)
    choice = SourceChoiceRepository(db_session).current(req.id)
    assert (choice.version, choice.basis, choice.feed_id, choice.proposal_id) == (1, "requester_pick", feed.id,
                                                                                  proposal.id)
    assert status(db_session, req) == "source_selected"
    assert ProposalRepository(db_session).get(proposals[BACKUP]).status == "pending"  # assumption 37
    assert count(db_session, SourceFetchRow) == 0  # the first fetch is its own transaction (the API's)


@pytest.mark.parametrize("accepted, reason", [(None, "not_accepted"), (BACKUP, "conditions_differ")])
def test_approval_missing_when_the_conditions_are_not_accepted_and_nothing_is_fetched(db_session, listed,
                                                                                       accepted, reason):
    """None: no acceptance at all. BACKUP: another source's conditions, not the ones in force for VENDOR."""
    req, proposals = listed()
    before = snapshot(db_session)
    result = pick(db_session, req, proposals[VENDOR], conditions(accepted) if accepted else None)
    assert isinstance(result, ApprovalMissing)
    assert (result.source.code, result.reason) == (VENDOR, reason)
    assert result.conditions == conditions(VENDOR)
    assert result.conditions.external_approvals == ["A signed data agreement with the vendor, held by your desk"]
    assert snapshot(db_session) == before  # no approval, feed, choice or fetch
    assert ProposalRepository(db_session).get(proposals[VENDOR]).status == "pending"
    assert status(db_session, req) == "awaiting_source_choice"


def test_approval_missing_when_the_conditions_changed_since_the_list(db_session, listed):
    req, proposals = listed()
    shown = conditions(VENDOR)
    changed = SourceCatalogue([e.model_copy(update={"conditions": shown.model_copy(
        update={"permitted_use": "Internal analysis only; no reporting at all"})}) if e.code == VENDOR else e
        for e in CATALOGUE.entries])
    before = snapshot(db_session)
    result = pick(db_session, req, proposals[VENDOR], shown, catalogue=changed)
    assert isinstance(result, ApprovalMissing) and result.reason == "conditions_differ"
    assert result.conditions == changed.get(VENDOR).conditions  # the ones in force, to accept again
    assert snapshot(db_session) == before
    assert isinstance(pick(db_session, req, proposals[VENDOR], result.conditions, catalogue=changed), SourcePicked)


def test_the_accepted_conditions_are_stored_on_the_choice(db_session, listed):
    req, proposals = listed()
    pick(db_session, req, proposals[VENDOR], conditions(VENDOR))
    choice = SourceChoiceRepository(db_session).current(req.id)
    assert (choice.accepted_conditions, choice.decided_by) == (conditions(VENDOR), "dev-requester")
    stored = db_session.execute(text("SELECT accepted_conditions FROM source_choices WHERE id = :id"),
                                {"id": choice.id}).scalar_one()
    assert stored == conditions(VENDOR).model_dump(mode="json")


def test_picking_again_needs_a_new_acceptance(db_session, listed):
    """After a release, the next pick is a new decision: the newly picked source's conditions, again."""
    req, proposals = listed()
    pick(db_session, req, proposals[BACKUP], conditions(BACKUP))
    SourceChoiceRepository(db_session).record_release(requirement_id=req.id, reason="requester_asked",
                                                      decided_by="dev-requester")
    RequirementRepository(db_session).advance(req.id, "awaiting_source_choice")
    assert isinstance(pick(db_session, req, proposals[VENDOR], None), ApprovalMissing)
    assert isinstance(pick(db_session, req, proposals[VENDOR], conditions(BACKUP)), ApprovalMissing)
    result = pick(db_session, req, proposals[VENDOR], conditions(VENDOR))
    assert isinstance(result, SourcePicked)
    assert [(c.version, c.action, c.source_code) for c in SourceChoiceRepository(db_session).history(req.id)] == [
        (1, "selected", BACKUP), (2, "released", BACKUP), (3, "selected", VENDOR)]


def test_a_second_requirement_picking_the_same_source_reuses_its_feed(db_session, listed):
    first, first_proposals = listed("first")
    second, second_proposals = listed("second")
    pick(db_session, first, first_proposals[VENDOR], conditions(VENDOR))
    pick(db_session, second, second_proposals[VENDOR], conditions(VENDOR))
    assert db_session.scalar(select(func.count()).select_from(FeedRow).where(FeedRow.source_code == VENDOR)) == 1


def test_only_the_requirements_own_requester_picks(db_session, listed):
    req, proposals = listed()
    other = User(id="dev-other-requester", display_name="Other", email="other@example.test", roles=["requester"])
    before = snapshot(db_session)
    with pytest.raises(NotTheRequester):
        pick(db_session, req, proposals[VENDOR], conditions(VENDOR), user=other)
    with pytest.raises(NotTheRequester):
        decline_source(db_session, requirement_id=req.id, user=other, proposal_id=proposals[VENDOR], note=None,
                       catalogue=CATALOGUE)
    assert snapshot(db_session) == before


def test_a_proposal_of_another_requirement_is_refused(db_session, listed):
    req, _ = listed("first")
    _, others = listed("second")
    with pytest.raises(ProposalForAnotherRequirement):
        pick(db_session, req, others[VENDOR], conditions(VENDOR))


def test_a_pick_is_refused_unless_the_requirement_awaits_a_choice(db_session, listed):
    req, proposals = listed()
    pick(db_session, req, proposals[VENDOR], conditions(VENDOR))
    with pytest.raises(RequirementNotInStatus):
        pick(db_session, req, proposals[BACKUP], conditions(BACKUP))


def test_a_decided_proposal_cannot_be_picked(db_session, listed):
    req, proposals = listed()
    decline_source(db_session, requirement_id=req.id, user=REQUESTER, proposal_id=proposals[VENDOR], note="no",
                   catalogue=CATALOGUE)
    with pytest.raises(ProposalAlreadyDecided):
        pick(db_session, req, proposals[VENDOR], conditions(VENDOR))


def test_declining_a_source_rejects_its_proposal_and_keeps_the_others(db_session, listed):
    req, proposals = listed()
    result = decline_source(db_session, requirement_id=req.id, user=REQUESTER, proposal_id=proposals[VENDOR],
                            note="We have no vendor agreement", catalogue=CATALOGUE)
    assert (result.source_code, result.status, result.remaining) == (VENDOR, "awaiting_source_choice", 1)
    assert ProposalRepository(db_session).get(proposals[VENDOR]).status == "rejected"
    assert ProposalRepository(db_session).approvals(proposals[VENDOR])[0].note == "We have no vendor agreement"
    assert status(db_session, req) == "awaiting_source_choice"


def test_declining_the_last_source_gives_no_source_found(db_session, listed):
    req, proposals = listed()
    for code in (VENDOR, BACKUP):
        result = decline_source(db_session, requirement_id=req.id, user=REQUESTER, proposal_id=proposals[code],
                                note=None, catalogue=CATALOGUE)
    assert (result.status, result.remaining) == ("no_source_found", 0)
    assert status(db_session, req) == "no_source_found"
