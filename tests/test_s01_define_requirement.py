"""Step 01 Define requirement (core/steps/s01_define_requirement.py).

Outcomes (CLAUDE.md): defined; element_not_in_glossary. The requirement is saved in both cases, with
its elements and the ticked terms (decision 3). Every requirement tells the backend team (decision 15);
ticked terms also tell the glossary owner. Phase 2 plan, assumptions 30 to 33.
"""

import pytest

from core.repositories.notifications import NotificationRepository
from core.repositories.requirements import RequirementRepository
from core.steps.errors import NothingToRequest
from core.steps.s01_define_requirement import Defined, ElementNotInGlossary, define_requirement
from ports.notify.inapp import InAppNotifier

RECIPIENTS = {"backend_team": ["dev-backend-team"], "glossary_owner": ["dev-glossary-owner"]}


@pytest.fixture
def define(db_session, glossary):
    def _define(*, element_keys=(), terms=(), query="volume", purpose="", recipients=None):
        return define_requirement(
            db_session, requester_id="dev-requester", query=query, purpose=purpose,
            element_keys=list(element_keys), terms=list(terms), notifier=InAppNotifier(db_session),
            recipients=RECIPIENTS if recipients is None else recipients,
            display_name=lambda uid: {"dev-requester": "Riley Requester"}.get(uid, uid),
        )

    return _define


def inbox(db_session, user_id):
    return [(n.subject, n.body) for n in NotificationRepository(db_session).list_for(user_id)]


def test_defined_when_no_terms_were_ticked(define, db_session):
    result = define(element_keys=["volume"], query="volume", purpose="Synthetic purpose.")
    assert isinstance(result, Defined)
    requirement = result.requirement
    assert (requirement.status, requirement.elements, requirement.terms) == ("defined", ["volume"], [])
    assert (requirement.title, requirement.description) == ("volume", "Synthetic purpose.")
    assert RequirementRepository(db_session).get(requirement.id) == requirement
    assert result.notified == ["dev-backend-team"]
    assert inbox(db_session, "dev-backend-team") == [
        ("New requirement: volume", "Requested by Riley Requester.\nElements: volume.\nTerms: none."),
    ]
    assert inbox(db_session, "dev-glossary-owner") == []


def test_element_not_in_glossary_saves_the_terms_and_tells_the_glossary_owner(define, db_session):
    result = define(element_keys=["volume"], terms=["esg score", "carbon footprint"],
                    query="volume, esg score and carbon footprint")
    assert isinstance(result, ElementNotInGlossary)
    assert result.terms == ["carbon footprint", "esg score"]
    assert (result.requirement.status, result.requirement.elements, result.requirement.terms) == (
        "defined", ["volume"], ["carbon footprint", "esg score"],
    )
    assert result.notified == ["dev-backend-team", "dev-glossary-owner"]
    body = "Requested by Riley Requester.\nElements: volume.\nTerms: carbon footprint, esg score."
    assert inbox(db_session, "dev-glossary-owner") == [("Terms not in the glossary", body)]
    assert inbox(db_session, "dev-backend-team") == [
        ("New requirement: volume, esg score and carbon footprint", body),
    ]


def test_terms_only(define):
    result = define(terms=["esg score"], query="esg score")
    assert isinstance(result, ElementNotInGlossary)
    assert (result.requirement.elements, result.requirement.terms) == ([], ["esg score"])


def test_nothing_to_request(define):
    with pytest.raises(NothingToRequest):
        define()


def test_title_is_the_query_cut_at_120_characters_and_purpose_is_optional(define):
    result = define(element_keys=["volume"], query="  " + "v" * 130 + "  ")
    assert result.requirement.title == "v" * 120
    assert result.requirement.description == ""


def test_a_role_with_nobody_notifies_nobody_and_says_so(define, db_session):
    result = define(element_keys=["volume"], terms=["esg score"], recipients={})
    assert result.notified == []
    assert RequirementRepository(db_session).get(result.requirement.id).terms == ["esg score"]
