"""The database guards on source_choices (phase 3 plan, assumption 50; migration 0009).

Every test writes raw SQL, so the repository's own checks are bypassed and only the database decides.
Each refused insert runs in its own savepoint, so one test can try several rows.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirements import RequirementRepository
from tests.conftest import dev_user_for

CONDITIONS = {"licence": "Synthetic licence", "permitted_use": "Internal only", "redistribution": "Not allowed",
              "external_approvals": []}

INSERT = text(
    "INSERT INTO source_choices (requirement_id, version, action, feed_id, source_code, basis, reason, "
    "proposal_id, accepted_conditions, decided_by) VALUES (:requirement_id, :version, :action, :feed_id, "
    ":source_code, :basis, :reason, :proposal_id, CAST(:accepted_conditions AS jsonb), :decided_by)"
)


class World:
    """One requirement of dev-requester and two feeds, syn_a and syn_b, with row builders."""

    def __init__(self, session, make_proposal):
        self.session, self._make_proposal = session, make_proposal
        self.requirement = RequirementRepository(session).create(requester_id="dev-requester", title="open price")
        feeds = FeedRepository(session)
        self.feed = {code: feeds.create_candidate(code=code, name=code, source_code=code, connector="local_file",
                                                  source_ref=f"files/{code}.csv").id for code in ("syn_a", "syn_b")}

    def proposal(self, source_code="syn_a", *, decision="approved", kind="source_list", requirement_id=None):
        proposal = self._make_proposal(kind=kind, roles=("requester",), payload={"source_code": source_code},
                                       requirement_id=requirement_id or self.requirement.id)
        if decision is not None:
            ProposalRepository(self.session).record_decision(proposal.id, user=dev_user_for("requester"),
                                                             decision=decision)
        return proposal.id

    def known(self, version=1, source="syn_a", **over):
        return {"version": version, "action": "selected", "source": source, "basis": "known_source", **over}

    def pick(self, version=1, source="syn_a", **over):
        values = {"version": version, "action": "selected", "source": source, "basis": "requester_pick",
                  "accepted_conditions": CONDITIONS, "decided_by": "dev-requester"}
        if "proposal_id" not in over:
            values["proposal_id"] = self.proposal(source)
        return {**values, **over}

    def release(self, version=2, source="syn_a", reason="source_down_n_days", **over):
        decided_by = "dev-requester" if reason == "requester_asked" else None
        return {"version": version, "action": "released", "source": source, "reason": reason,
                "decided_by": decided_by, **over}

    def insert(self, row):
        row = dict(row)
        source = row.pop("source")
        values = {"requirement_id": self.requirement.id, "feed_id": self.feed[source], "source_code": source,
                  "basis": None, "reason": None, "proposal_id": None, "accepted_conditions": None,
                  "decided_by": None, **row}
        if isinstance(values["accepted_conditions"], (dict, list)):
            values["accepted_conditions"] = json.dumps(values["accepted_conditions"])
        self.session.execute(INSERT, values)

    def refused(self, row, match):
        with pytest.raises(DBAPIError, match=match):
            with self.session.begin_nested():
                self.insert(row)


@pytest.fixture
def world(db_session, make_proposal):
    return World(db_session, make_proposal)


# ---------------------------------------------------------------- line 1: versions


def test_a_version_must_be_the_next_one(world):
    world.refused(world.known(version=2), "version 2 is not the next version; expected 1")
    world.insert(world.known(version=1))
    world.refused(world.release(version=3), "version 3 is not the next version; expected 2")
    world.refused(world.release(version=1), "version 1 is not the next version; expected 2")
    world.insert(world.release(version=2))


# ---------------------------------------------------------------- line 2: what follows what


def test_the_first_row_must_be_a_selection(world):
    world.refused(world.release(version=1), "the first source choice of a requirement must be a selection")


def test_a_release_must_follow_a_selection_of_the_same_source(world):
    world.insert(world.known())
    world.refused(world.release(source="syn_b"), "a release must name the selected feed and source")
    world.insert(world.release())
    world.refused(world.release(version=3), "a release must follow a selection")


def test_a_selection_cannot_follow_a_selection(world):
    world.insert(world.known())
    world.refused(world.known(version=2, source="syn_b"), "a selection cannot follow a selection")


# ---------------------------------------------------------------- line 3: once per requirement


def test_a_released_source_cannot_be_selected_again(world):
    world.insert(world.known())
    world.insert(world.release())
    world.refused(world.pick(version=3, source="syn_a"), "uq_source_choices_selected_source")
    world.insert(world.pick(version=3, source="syn_b"))


# ---------------------------------------------------------------- line 4: basis and reason


def test_a_selection_has_a_basis_and_a_release_has_a_reason(world):
    world.refused(world.known(basis=None), "ck_source_choices_selection_or_release")
    world.refused(world.known(reason="source_down_n_days"), "ck_source_choices_selection_or_release")
    world.refused(world.known(basis="guess"), "ck_source_choices_basis")
    world.insert(world.known())
    world.refused(world.release(reason=None), "ck_source_choices_selection_or_release")
    world.refused(world.release(basis="known_source"), "ck_source_choices_selection_or_release")
    world.refused(world.release(reason="bored"), "ck_source_choices_reason")
    world.refused(world.release(action="paused"), "ck_source_choices_action")


# ---------------------------------------------------------------- line 5: a pick and its proposal


def test_a_pick_needs_an_approved_source_list_proposal_of_the_same_requirement_and_source(world, db_session):
    world.refused(world.pick(proposal_id=world.proposal(decision=None)), "is pending, not approved")
    world.refused(world.pick(proposal_id=world.proposal(decision="rejected")), "is rejected, not approved")
    world.refused(world.pick(proposal_id=world.proposal(kind="scope")), "is a scope proposal, not source_list")
    world.refused(world.pick(proposal_id=uuid.uuid4()), "does not exist")
    other = RequirementRepository(db_session).create(requester_id="dev-requester", title="other")
    world.refused(world.pick(proposal_id=world.proposal(requirement_id=other.id)), "is for another requirement")
    world.refused(world.pick(proposal_id=world.proposal("syn_b")), "names source syn_b, not syn_a")
    world.insert(world.pick())


def test_a_pick_needs_the_accepted_conditions_and_the_requester(world):
    world.refused(world.pick(accepted_conditions=None), "ck_source_choices_pick_has_its_acceptance")
    world.refused(world.pick(accepted_conditions=["not", "an", "object"]),
                  "ck_source_choices_conditions_are_an_object")
    world.refused(world.pick(decided_by=None), "ck_source_choices_decided_by_only_for_a_person")


@pytest.mark.parametrize("decided_by", ["dev-someone-else", " ", ""])
def test_a_pick_is_decided_by_the_requirements_own_requester(world, decided_by):
    world.refused(world.pick(decided_by=decided_by), "only the requirement's requester")


# ---------------------------------------------------------------- line 6: a known source


def test_a_known_source_row_has_no_proposal(world):
    world.refused(world.known(proposal_id=world.proposal()), "ck_source_choices_pick_has_its_acceptance")
    world.refused(world.known(accepted_conditions=CONDITIONS), "ck_source_choices_pick_has_its_acceptance")
    world.refused(world.known(decided_by="dev-requester"), "ck_source_choices_decided_by_only_for_a_person")
    world.insert(world.known())


# ---------------------------------------------------------------- line 7: who released


def test_a_release_names_the_requester_only_when_asked(world):
    world.insert(world.known())
    world.refused(world.release(reason="requester_asked", decided_by=None),
                  "ck_source_choices_decided_by_only_for_a_person")
    world.refused(world.release(decided_by="dev-requester"), "ck_source_choices_decided_by_only_for_a_person")
    world.refused(world.release(reason="requester_asked", decided_by="dev-someone-else"),
                  "only the requirement's requester")
    world.refused(world.release(proposal_id=world.proposal()), "ck_source_choices_pick_has_its_acceptance")
    world.insert(world.release(reason="requester_asked"))


# ---------------------------------------------------------------- NULL never passes by accident

NULL_CASES = {
    "basis on a selection": ("known", {"basis": None}, "ck_source_choices_selection_or_release"),
    "reason on a release": ("release", {"reason": None}, "ck_source_choices_selection_or_release"),
    "proposal_id on a pick": ("pick", {"proposal_id": None}, "needs an approved source_list proposal"),
    "accepted_conditions on a pick": ("pick", {"accepted_conditions": None},
                                      "ck_source_choices_pick_has_its_acceptance"),
    "decided_by on a pick": ("pick", {"decided_by": None}, "ck_source_choices_decided_by_only_for_a_person"),
    "decided_by on a requester_asked release": ("release", {"reason": "requester_asked", "decided_by": None},
                                                "ck_source_choices_decided_by_only_for_a_person"),
}


@pytest.mark.parametrize("case", sorted(NULL_CASES))
def test_null_never_passes_a_source_choice_check(world, case):
    builder, over, match = NULL_CASES[case]
    if builder == "release":
        world.insert(world.known())
    world.refused(getattr(world, builder)(**over), match)
