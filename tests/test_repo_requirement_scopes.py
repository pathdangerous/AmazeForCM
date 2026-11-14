"""RequirementScopeRepository and the requirement_scopes guards (phase 3 plan, assumption 28; 0009).

A scope row is written only for a scope proposal the requester approved, for the same requirement, and
holds exactly what was proposed: the requester confirms or rejects, never edits (assumption 29).
Versioned per requirement; append-only.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.repositories.proposals import ProposalRepository
from core.repositories.requirement_scopes import RequirementScopeRepository
from core.repositories.requirements import RequirementRepository
from tests.conftest import dev_user_for

SCOPE = {"visibility": "public", "provider": "External data providers",
         "where_to_search": ["exchanges", "market_data_vendors"], "reason": "Nothing says private."}


@pytest.fixture
def requirement(db_session):
    return RequirementRepository(db_session).create(requester_id="dev-requester", title="open price")


@pytest.fixture
def scope_proposal(db_session, make_proposal, requirement):
    def _make(decision="approved", payload=None, requirement_id=None, kind="scope"):
        proposal = make_proposal(kind=kind, roles=("requester",), payload=payload or SCOPE,
                                 requirement_id=requirement_id or requirement.id)
        if decision is not None:
            ProposalRepository(db_session).record_decision(proposal.id, user=dev_user_for("requester"),
                                                           decision=decision)
        return proposal.id

    return _make


@pytest.fixture
def repo(db_session):
    return RequirementScopeRepository(db_session)


def _add(repo, requirement, proposal_id, **over):
    fields = {"visibility": SCOPE["visibility"], "provider": SCOPE["provider"],
              "where_to_search": SCOPE["where_to_search"], **over}
    return repo.add_version(requirement_id=requirement.id, proposal_id=proposal_id, confirmed_by="dev-requester",
                            **fields)


def test_add_version_and_current(repo, requirement, scope_proposal):
    assert repo.current(requirement.id) is None
    first = _add(repo, requirement, scope_proposal())
    assert (first.version, first.visibility, first.where_to_search, first.confirmed_by) == (
        1, "public", ["exchanges", "market_data_vendors"], "dev-requester")
    second = _add(repo, requirement, scope_proposal(payload={**SCOPE, "visibility": "private",
                                                             "where_to_search": ["internal_systems"]}),
                  visibility="private", where_to_search=["internal_systems"])
    assert second.version == 2
    assert repo.current(requirement.id) == second


@pytest.mark.parametrize("bad", [{"visibility": "secret"}, {"where_to_search": []}, {"provider": " "}])
def test_the_scope_is_validated(repo, requirement, scope_proposal, bad):
    with pytest.raises(ValidationError):
        _add(repo, requirement, scope_proposal(), **bad)


RAW = text(
    "INSERT INTO requirement_scopes (requirement_id, version, visibility, provider, where_to_search, "
    "proposal_id, confirmed_by) VALUES (:r, :v, :visibility, :provider, CAST(:where AS text[]), :p, :by)"
)


def _raw(db_session, requirement, proposal_id, **over):
    values = {"r": requirement.id, "v": 1, "visibility": SCOPE["visibility"], "provider": SCOPE["provider"],
              "where": SCOPE["where_to_search"], "p": proposal_id, "by": "dev-requester", **over}
    with db_session.begin_nested():
        db_session.execute(RAW, values)


def test_a_scope_needs_an_approved_scope_proposal_of_the_same_requirement(db_session, requirement,
                                                                          scope_proposal):
    other = RequirementRepository(db_session).create(requester_id="dev-requester", title="other")
    for proposal_id, match in [
        (scope_proposal(decision=None), "is pending, not approved"),
        (scope_proposal(decision="rejected"), "is rejected, not approved"),
        (scope_proposal(kind="source_list"), "is a source_list proposal, not scope"),
        (scope_proposal(requirement_id=other.id), "is for another requirement"),
    ]:
        with pytest.raises(DBAPIError, match=match):
            _raw(db_session, requirement, proposal_id)
    _raw(db_session, requirement, scope_proposal())


@pytest.mark.parametrize("over", [{"visibility": "private"}, {"provider": "Someone else"},
                                  {"where": ["exchanges"]}])
def test_a_scope_holds_exactly_what_was_proposed(db_session, requirement, scope_proposal, over):
    with pytest.raises(DBAPIError, match="differs from what proposal"):
        _raw(db_session, requirement, scope_proposal(), **over)


def test_a_scope_is_confirmed_by_the_requirements_own_requester(db_session, requirement, scope_proposal):
    with pytest.raises(DBAPIError, match="only the requirement's requester"):
        _raw(db_session, requirement, scope_proposal(), by="dev-someone-else")


def test_database_refuses_a_repeated_version_and_an_empty_search(db_session, requirement, scope_proposal):
    _raw(db_session, requirement, scope_proposal())
    with pytest.raises(DBAPIError, match="uq_requirement_scopes_requirement_id_version"):
        _raw(db_session, requirement, scope_proposal())
    with pytest.raises(DBAPIError, match="ck_requirement_scopes_where_to_search"):
        _raw(db_session, requirement, scope_proposal(payload={**SCOPE, "where_to_search": []}), v=2, where=[])
