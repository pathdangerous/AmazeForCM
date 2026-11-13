"""RequirementRepository: what a requester asked for (step 01) and its status."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.repositories.errors import RequirementNotFound
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
