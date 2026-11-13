"""RequirementRepository: what a requester asked for (step 01) and its status."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from core.repositories.errors import RequirementNotFound, UnknownGlossaryElement
from core.repositories.requirements import RequirementRepository


@pytest.fixture
def repo(db_session):
    return RequirementRepository(db_session)


def test_create_starts_defined(repo):
    req = repo.create(requester_id="dev-requester", title="Daily close prices for synthetic bonds",
                      description="Synthetic request used in tests.")
    assert req.status == "defined"
    assert repo.get(req.id) == req


def test_list_by_requester_newest_first(repo):
    first = repo.create(requester_id="dev-requester", title="First")
    second = repo.create(requester_id="dev-requester", title="Second")
    other = repo.create(requester_id="dev-someone-else", title="Other")
    assert [r.id for r in repo.list(requester_id="dev-requester")] == [second.id, first.id]
    assert {r.id for r in repo.list()} == {first.id, second.id, other.id}


@pytest.mark.parametrize("status", ["onboarding", "ready", "access_restricted", "rejected"])
def test_set_status(repo, status):
    req = repo.create(requester_id="dev-requester", title="T")
    assert repo.set_status(req.id, status).status == status
    assert repo.get(req.id).status == status


def test_unknown_status_and_unknown_requirement(repo):
    req = repo.create(requester_id="dev-requester", title="T")
    with pytest.raises(ValidationError):
        repo.set_status(req.id, "lost")
    with pytest.raises(RequirementNotFound):
        repo.get(uuid.uuid4())
    with pytest.raises(RequirementNotFound):
        repo.set_status(uuid.uuid4(), "ready")


@pytest.mark.parametrize("bad", [{"title": " "}, {"requester_id": ""}])
def test_create_needs_requester_and_title(repo, bad):
    with pytest.raises(ValidationError):
        repo.create(**{"requester_id": "dev-requester", "title": "T", **bad})


def test_database_refuses_unknown_status(db_session):
    with pytest.raises(IntegrityError, match="ck_requirements_status"):
        db_session.execute(text(
            "INSERT INTO requirements (requester_id, title, status) VALUES ('dev-requester', 'T', 'lost')"
        ))


# ---- phase 2: the glossary elements and the unmatched terms a requirement asks for


def test_a_requirement_without_elements_or_terms_has_empty_lists(repo):
    req = repo.create(requester_id="dev-requester", title="T")
    assert (req.elements, req.terms) == ([], [])


def test_create_with_elements_and_terms(repo, glossary):
    req = repo.create(requester_id="dev-requester", title="Volume and ESG score",
                      element_keys=["volume", "close_price"], terms=["esg score", "carbon footprint"])
    assert req.elements == ["close_price", "volume"]
    assert req.terms == ["carbon footprint", "esg score"]
    assert repo.get(req.id) == req
    assert repo.list(requester_id="dev-requester") == [req]


def test_list_returns_each_requirements_own_elements(repo, glossary):
    first = repo.create(requester_id="dev-requester", title="First", element_keys=["volume"])
    second = repo.create(requester_id="dev-requester", title="Second", terms=["esg score"])
    assert [(r.id, r.elements, r.terms) for r in repo.list(requester_id="dev-requester")] == [
        (second.id, [], ["esg score"]),
        (first.id, ["volume"], []),
    ]


def test_set_status_keeps_elements_and_terms(repo, glossary):
    req = repo.create(requester_id="dev-requester", title="T", element_keys=["volume"], terms=["esg score"])
    changed = repo.set_status(req.id, "onboarding")
    assert (changed.status, changed.elements, changed.terms) == ("onboarding", ["volume"], ["esg score"])


def test_unknown_element_is_refused_and_nothing_is_created(repo, glossary):
    with pytest.raises(UnknownGlossaryElement, match="esg_score"):
        repo.create(requester_id="dev-requester", title="T", element_keys=["volume", "esg_score"])
    assert repo.list() == []


@pytest.mark.parametrize(
    "bad",
    [
        {"element_keys": ["volume", "volume"]},
        {"element_keys": ["Volume"]},
        {"terms": [" "]},
        {"terms": ["esg score", "esg score"]},
    ],
)
def test_elements_and_terms_are_validated(repo, glossary, bad):
    with pytest.raises(ValidationError):
        repo.create(**{"requester_id": "dev-requester", "title": "T", **bad})


def test_database_refuses_an_element_key_that_is_not_in_the_glossary(repo, glossary, db_session):
    req = repo.create(requester_id="dev-requester", title="T")
    with pytest.raises(DBAPIError, match="element_key esg_score is not in the glossary"):
        db_session.execute(
            text("INSERT INTO requirement_elements (requirement_id, element_key) VALUES (:r, 'esg_score')"),
            {"r": req.id},
        )


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("INSERT INTO requirement_elements (requirement_id, element_key) VALUES (:r, 'volume')",
         "uq_requirement_elements_requirement_id_element_key"),
        ("INSERT INTO requirement_terms (requirement_id, term) VALUES (:r, 'esg score')",
         "uq_requirement_terms_requirement_id_term"),
        ("INSERT INTO requirement_terms (requirement_id, term) VALUES (:r, ' ')", "ck_requirement_terms_term_present"),
    ],
)
def test_database_refuses_a_repeated_element_or_term_and_a_blank_term(repo, glossary, db_session, statement, constraint):
    req = repo.create(requester_id="dev-requester", title="T", element_keys=["volume"], terms=["esg score"])
    with pytest.raises(IntegrityError, match=constraint):
        db_session.execute(text(statement), {"r": req.id})
