"""SourceChoiceRepository: which source (and its candidate feed) a requirement was handed to.

Versioned per requirement since 0009 (phase 3 plan, assumptions 49 to 53): every write is the next
version, a selection or a release; the highest version is current. Insert only (append-only table).
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from core.repositories.errors import (
    FeedNotFound,
    NothingToRelease,
    RequirementAlreadyHasSource,
    RequirementNotFound,
    SourceAlreadyReleased,
)
from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from ports.connectors.catalogue import SourceConditions
from tests.conftest import dev_user_for

CONDITIONS = SourceConditions(licence="Synthetic licence", permitted_use="Internal only",
                              redistribution="Not allowed", external_approvals=[])


@pytest.fixture
def repo(db_session):
    return SourceChoiceRepository(db_session)


@pytest.fixture
def feeds(db_session):
    repository = FeedRepository(db_session)
    return {code: repository.create_candidate(code=code, name=code, source_code=code, connector="local_file",
                                              source_ref=f"files/{code}.csv") for code in ("syn_a", "syn_b")}


@pytest.fixture
def feed(feeds):
    return feeds["syn_a"]


@pytest.fixture
def requirement(db_session):
    def _make(title="volume"):
        return RequirementRepository(db_session).create(requester_id="dev-requester", title=title)

    return _make


@pytest.fixture
def picked(db_session, make_proposal):
    """An approved source_list proposal for this requirement and source."""
    def _make(requirement_id, source_code):
        proposal = make_proposal(kind="source_list", roles=("requester",), payload={"source_code": source_code},
                                 requirement_id=requirement_id)
        ProposalRepository(db_session).record_decision(proposal.id, user=dev_user_for("requester"),
                                                       decision="approved")
        return proposal.id

    return _make


def _pick(repo, picked, req, feed):
    return repo.record_pick(requirement_id=req.id, feed_id=feed.id, source_code=feed.source_code,
                            proposal_id=picked(req.id, feed.source_code), accepted_conditions=CONDITIONS,
                            decided_by="dev-requester")


def test_record_known_and_read_back(repo, feed, requirement):
    req = requirement()
    choice = repo.record_known(requirement_id=req.id, feed_id=feed.id, source_code="syn_a")
    assert (choice.requirement_id, choice.version, choice.action, choice.feed_id, choice.source_code,
            choice.basis) == (req.id, 1, "selected", feed.id, "syn_a", "known_source")
    assert (choice.reason, choice.proposal_id, choice.accepted_conditions, choice.decided_by) == (None,) * 4
    assert repo.current(req.id) == choice
    assert repo.current(requirement("other").id) is None


def test_record_pick_stores_the_accepted_conditions(repo, feed, requirement, picked):
    req = requirement()
    choice = _pick(repo, picked, req, feed)
    assert (choice.version, choice.action, choice.basis, choice.decided_by) == (1, "selected", "requester_pick",
                                                                               "dev-requester")
    assert choice.accepted_conditions == CONDITIONS
    assert choice.proposal_id is not None


def test_current_is_the_highest_version(repo, feeds, requirement, picked):
    req = requirement()
    repo.record_known(requirement_id=req.id, feed_id=feeds["syn_a"].id, source_code="syn_a")
    released = repo.record_release(requirement_id=req.id, reason="source_down_n_days")
    assert repo.current(req.id) == released
    assert (released.version, released.action, released.source_code, released.reason, released.decided_by) == (
        2, "released", "syn_a", "source_down_n_days", None)
    third = _pick(repo, picked, req, feeds["syn_b"])
    assert repo.current(req.id) == third and third.version == 3


def test_history_lists_every_version(repo, feeds, requirement, picked):
    req = requirement()
    repo.record_known(requirement_id=req.id, feed_id=feeds["syn_a"].id, source_code="syn_a")
    repo.record_release(requirement_id=req.id, reason="requester_asked", decided_by="dev-requester")
    _pick(repo, picked, req, feeds["syn_b"])
    assert [(c.version, c.action, c.source_code) for c in repo.history(req.id)] == [
        (1, "selected", "syn_a"), (2, "released", "syn_a"), (3, "selected", "syn_b")]
    assert repo.released_sources(req.id) == {"syn_a"}


def test_requirements_selecting_a_feed_leaves_out_those_that_released_it(repo, feed, requirement):
    first, second, third = requirement("first"), requirement("second"), requirement("third")
    for req in (first, second, third):
        repo.record_known(requirement_id=req.id, feed_id=feed.id, source_code="syn_a")
    repo.record_release(requirement_id=second.id, reason="source_down_n_days")
    assert repo.requirements_selecting(feed.id) == [first.id, third.id]


def test_a_selection_on_a_current_selection_is_refused(repo, feeds, requirement, picked):
    req = requirement()
    repo.record_known(requirement_id=req.id, feed_id=feeds["syn_a"].id, source_code="syn_a")
    with pytest.raises(RequirementAlreadyHasSource):
        repo.record_known(requirement_id=req.id, feed_id=feeds["syn_b"].id, source_code="syn_b")
    with pytest.raises(RequirementAlreadyHasSource):
        _pick(repo, picked, req, feeds["syn_b"])


def test_nothing_to_release_without_a_current_selection(repo, feed, requirement):
    req = requirement()
    with pytest.raises(NothingToRelease):
        repo.record_release(requirement_id=req.id, reason="source_down_n_days")
    repo.record_known(requirement_id=req.id, feed_id=feed.id, source_code="syn_a")
    repo.record_release(requirement_id=req.id, reason="source_down_n_days")
    with pytest.raises(NothingToRelease):
        repo.record_release(requirement_id=req.id, reason="source_down_n_days")


def test_a_released_source_is_refused_before_the_database_sees_it(repo, feed, requirement, picked):
    req = requirement()
    repo.record_known(requirement_id=req.id, feed_id=feed.id, source_code="syn_a")
    repo.record_release(requirement_id=req.id, reason="source_down_n_days")
    with pytest.raises(SourceAlreadyReleased):
        _pick(repo, picked, req, feed)


@pytest.mark.parametrize("reason, decided_by", [("requester_asked", None), ("source_down_n_days", "dev-requester"),
                                                ("bored", None)])
def test_a_release_names_the_requester_only_when_asked(repo, feed, requirement, reason, decided_by):
    req = requirement()
    repo.record_known(requirement_id=req.id, feed_id=feed.id, source_code="syn_a")
    with pytest.raises(ValueError):
        repo.record_release(requirement_id=req.id, reason=reason, decided_by=decided_by)


def test_unknown_requirement_or_feed_and_a_source_that_is_not_the_feeds(repo, feed, requirement):
    with pytest.raises(RequirementNotFound):
        repo.record_known(requirement_id=uuid.uuid4(), feed_id=feed.id, source_code="syn_a")
    with pytest.raises(FeedNotFound):
        repo.record_known(requirement_id=requirement().id, feed_id=uuid.uuid4(), source_code="syn_a")
    with pytest.raises(ValueError, match="syn_other"):
        repo.record_known(requirement_id=requirement().id, feed_id=feed.id, source_code="syn_other")


RAW = ("INSERT INTO source_choices (requirement_id, version, action, feed_id, source_code, basis) "
       "VALUES (:r, :v, 'selected', :f, 'syn_a', :basis)")


def test_database_refuses_an_unknown_basis(db_session, feed, requirement):
    with pytest.raises(IntegrityError, match="ck_source_choices_basis"):
        db_session.execute(text(RAW), {"r": requirement().id, "v": 1, "f": feed.id, "basis": "guess"})


def test_database_refuses_a_second_row_with_the_same_version(db_session, feed, requirement):
    """The version trigger refuses it first; the unique constraint is there for two concurrent inserts."""
    req = requirement()
    db_session.execute(text(RAW), {"r": req.id, "v": 1, "f": feed.id, "basis": "known_source"})
    with pytest.raises(DBAPIError, match="version 1 is not the next version"):
        db_session.execute(text(RAW), {"r": req.id, "v": 1, "f": feed.id, "basis": "known_source"})


def test_the_version_is_unique_per_requirement(db_session):
    names = db_session.execute(text(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'source_choices'::regclass AND contype = 'u'"
    )).scalars().all()
    assert names == ["uq_source_choices_requirement_id_version"]


def test_database_refuses_an_update(db_session, repo, feed, requirement):
    choice = repo.record_known(requirement_id=requirement().id, feed_id=feed.id, source_code="syn_a")
    with pytest.raises(DBAPIError, match="source_choices is append-only"):
        db_session.execute(text("UPDATE source_choices SET source_code = 'x' WHERE id = :id"), {"id": choice.id})
