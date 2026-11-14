"""SourceChoiceRepository: which source (and its candidate feed) a requirement was handed to.

One row per requirement, append-only (phase 3 plan, assumption 10). In 3a the only basis is
known_source; 3b adds the requester's pick.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from core.repositories.errors import FeedNotFound, RequirementAlreadyHasSource, RequirementNotFound
from core.repositories.feeds import FeedRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository


@pytest.fixture
def repo(db_session):
    return SourceChoiceRepository(db_session)


@pytest.fixture
def feed(db_session):
    return FeedRepository(db_session).create_candidate(
        code="syn_exchange_volumes", name="Exchange volumes", source_code="syn_exchange_volumes",
        connector="local_file", source_ref="files/exchange_volumes.csv",
    )


@pytest.fixture
def requirement(db_session):
    def _make(title="volume"):
        return RequirementRepository(db_session).create(requester_id="dev-requester", title=title)

    return _make


def test_record_known_and_read_back(repo, feed, requirement):
    req = requirement()
    choice = repo.record_known(requirement_id=req.id, feed_id=feed.id, source_code="syn_exchange_volumes")
    assert (choice.requirement_id, choice.feed_id, choice.source_code, choice.basis) == (
        req.id, feed.id, "syn_exchange_volumes", "known_source",
    )
    assert repo.for_requirement(req.id) == choice
    assert repo.for_requirement(requirement("other").id) is None


def test_requirements_for_feed_oldest_first(repo, feed, requirement):
    first, second = requirement("first"), requirement("second")
    repo.record_known(requirement_id=first.id, feed_id=feed.id, source_code="syn_exchange_volumes")
    repo.record_known(requirement_id=second.id, feed_id=feed.id, source_code="syn_exchange_volumes")
    assert repo.requirements_for_feed(feed.id) == [first.id, second.id]


def test_one_choice_per_requirement(repo, feed, requirement):
    req = requirement()
    repo.record_known(requirement_id=req.id, feed_id=feed.id, source_code="syn_exchange_volumes")
    with pytest.raises(RequirementAlreadyHasSource):
        repo.record_known(requirement_id=req.id, feed_id=feed.id, source_code="syn_exchange_volumes")


def test_unknown_requirement_or_feed_and_a_source_that_is_not_the_feeds(repo, feed, requirement):
    with pytest.raises(RequirementNotFound):
        repo.record_known(requirement_id=uuid.uuid4(), feed_id=feed.id, source_code="syn_exchange_volumes")
    with pytest.raises(FeedNotFound):
        repo.record_known(requirement_id=requirement().id, feed_id=uuid.uuid4(), source_code="syn_exchange_volumes")
    with pytest.raises(ValueError, match="syn_other"):
        repo.record_known(requirement_id=requirement().id, feed_id=feed.id, source_code="syn_other")


RAW = ("INSERT INTO source_choices (requirement_id, feed_id, source_code, basis) "
       "VALUES (:r, :f, 'syn_exchange_volumes', :basis)")


def test_database_refuses_an_unknown_basis(db_session, feed, requirement):
    with pytest.raises(IntegrityError, match="ck_source_choices_basis"):
        db_session.execute(text(RAW), {"r": requirement().id, "f": feed.id, "basis": "guess"})


def test_database_refuses_a_second_choice(db_session, feed, requirement):
    req = requirement()
    db_session.execute(text(RAW), {"r": req.id, "f": feed.id, "basis": "known_source"})
    with pytest.raises(IntegrityError, match="uq_source_choices_requirement_id"):
        db_session.execute(text(RAW), {"r": req.id, "f": feed.id, "basis": "known_source"})


def test_database_refuses_an_update(db_session, repo, feed, requirement):
    choice = repo.record_known(requirement_id=requirement().id, feed_id=feed.id, source_code="syn_exchange_volumes")
    with pytest.raises(DBAPIError, match="source_choices is append-only"):
        db_session.execute(text("UPDATE source_choices SET source_code = 'x' WHERE id = :id"), {"id": choice.id})
