"""Release a selected source (core/steps/release.py; phase 3 plan, assumptions 49 to 55).

The requester releases a source_selected requirement's source (reason requester_asked), or the fetch's
N-day rule does (source_down_n_days; tested in test_s4_fetch.py). A release is the next source_choices
version. The requirement goes to awaiting_source_choice if a listed source is left, else to
no_source_found. Release has no CLAUDE.md outcome name, so none is invented.
"""

from __future__ import annotations

import pytest

from core.repositories.errors import RequirementNotInStatus
from core.repositories.notifications import NotificationRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.steps.errors import NotTheRequester
from core.steps.known_source import find_known_source
from core.steps.release import Released, release_source
from core.steps.s2_sources import listed_sources
from core.steps.s3_pick import pick_source
from ports.identity.base import User
from ports.notify.inapp import InAppNotifier
from tests.conftest import dev_user_for
from tests.test_s3_pick import BACKUP, CATALOGUE, VENDOR, conditions, listed  # noqa: F401 (fixture)

REQUESTER = dev_user_for("requester")
TEAM = ["dev-backend-team"]


def release(db_session, req, user=REQUESTER, reason="requester_asked"):
    return release_source(db_session, requirement_id=req.id, reason=reason, user=user, catalogue=CATALOGUE,
                          notifier=InAppNotifier(db_session), backend_team=TEAM)


def status(db_session, req):
    return RequirementRepository(db_session).get(req.id).status


@pytest.fixture
def picked_backup(db_session, listed):
    """open price, with BACKUP picked (it has no file, so it stays source_selected) and VENDOR still listed."""
    req, proposals = listed()
    pick_source(db_session, requirement_id=req.id, user=REQUESTER, proposal_id=proposals[BACKUP],
                accepted_conditions=conditions(BACKUP), catalogue=CATALOGUE)
    return req, proposals


@pytest.fixture
def known_accruals(db_session, glossary):
    """Row B: accrued interest, on the known source syn_offline_accruals (source_selected)."""
    req = RequirementRepository(db_session).create(requester_id="dev-requester", title="accrued interest",
                                                   element_keys=["accrued_interest"])
    find_known_source(db_session, requirement_id=req.id, catalogue=CATALOGUE)
    return req


def test_the_requester_releases_a_source_that_did_not_answer(db_session, picked_backup):
    req, _ = picked_backup
    result = release(db_session, req)
    assert isinstance(result, Released)
    assert (result.source_code, result.source_name, result.reason, result.version) == (
        BACKUP, "Backup end-of-day prices", "requester_asked", 2)
    choice = SourceChoiceRepository(db_session).current(req.id)
    assert (choice.action, choice.source_code, choice.reason, choice.decided_by) == (
        "released", BACKUP, "requester_asked", "dev-requester")


def test_release_goes_to_awaiting_source_choice_when_a_listed_source_is_left(db_session, picked_backup):
    req, _ = picked_backup
    assert release(db_session, req).status == "awaiting_source_choice"
    assert status(db_session, req) == "awaiting_source_choice"
    assert [c.source.code for c in listed_sources(db_session, req.id, CATALOGUE)] == [VENDOR]


def test_release_goes_to_no_source_found_when_none_is_left(db_session, known_accruals):
    """A known source: S1 and S2 never ran, so nothing is listed (row K)."""
    assert release(db_session, known_accruals).status == "no_source_found"
    assert status(db_session, known_accruals) == "no_source_found"


@pytest.mark.parametrize("start", ["onboarding", "awaiting_source_choice", "defined"])
def test_only_a_source_selected_requirement_can_be_released(db_session, picked_backup, start):
    from sqlalchemy import text

    req, _ = picked_backup
    db_session.execute(text("UPDATE requirements SET status = :s WHERE id = :id"), {"s": start, "id": req.id})
    db_session.expire_all()
    with pytest.raises(RequirementNotInStatus):
        release(db_session, req)
    assert SourceChoiceRepository(db_session).current(req.id).action == "selected"


def test_only_the_requirements_own_requester_releases(db_session, picked_backup):
    req, _ = picked_backup
    other = User(id="dev-other-requester", display_name="Other", email="other@example.test", roles=["requester"])
    with pytest.raises(NotTheRequester):
        release(db_session, req, user=other)
    with pytest.raises(ValueError):
        release(db_session, req, user=None)  # requester_asked names the requester


def test_a_released_source_is_not_listed_or_picked_again(db_session, picked_backup):
    from core.repositories.errors import ProposalAlreadyDecided

    req, proposals = picked_backup
    release(db_session, req)
    assert BACKUP not in [c.source.code for c in listed_sources(db_session, req.id, CATALOGUE)]
    with pytest.raises(ProposalAlreadyDecided):  # its proposal was approved by the first pick
        pick_source(db_session, requirement_id=req.id, user=REQUESTER, proposal_id=proposals[BACKUP],
                    accepted_conditions=conditions(BACKUP), catalogue=CATALOGUE)


def test_picking_again_adds_the_next_version(db_session, picked_backup):
    req, proposals = picked_backup
    release(db_session, req)
    pick_source(db_session, requirement_id=req.id, user=REQUESTER, proposal_id=proposals[VENDOR],
                accepted_conditions=conditions(VENDOR), catalogue=CATALOGUE)
    assert [(c.version, c.action, c.source_code) for c in SourceChoiceRepository(db_session).history(req.id)] == [
        (1, "selected", BACKUP), (2, "released", BACKUP), (3, "selected", VENDOR)]
    assert status(db_session, req) == "source_selected"


def test_a_candidate_rejected_release_comes_from_onboarding_or_source_selected(db_session, picked_backup):
    """Phase 4: the sign-off's rejection releases by rule, naming nobody (the person is on the sign-off)."""
    from core.repositories.feeds import FeedRepository
    from tests.onboarded import rejected_feed

    req, _ = picked_backup
    RequirementRepository(db_session).advance(req.id, "onboarding")
    rejected_feed(db_session, FeedRepository(db_session).get_by_source(BACKUP).id)
    with pytest.raises(ValueError):
        release(db_session, req, user=REQUESTER, reason="candidate_rejected")
    result = release(db_session, req, user=None, reason="candidate_rejected")
    assert (result.reason, result.status) == ("candidate_rejected", "awaiting_source_choice")


def test_the_backend_team_is_told_once_per_release(db_session, picked_backup):
    req, _ = picked_backup
    before = len(NotificationRepository(db_session).list_for("dev-backend-team"))
    release(db_session, req)
    told = NotificationRepository(db_session).list_for("dev-backend-team")
    assert len(told) == before + 1
    assert told[0].subject == f"Source released: {BACKUP}"
    assert "requester_asked" in told[0].body and str(req.id) in told[0].body
