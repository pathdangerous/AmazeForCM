"""ConsumerRequestRepository: every "Request this data" and its routing outcome (decision 7).

The table is append-only. A requirement is linked exactly when the outcome is raise_requirement.
"""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.repositories.consumer_requests import ConsumerRequestRepository
from core.repositories.errors import ConsumerRequestNotFound, RequirementNotFound, UnknownGlossaryElement
from core.repositories.requirements import RequirementRepository

REUSE = {
    "requester_id": "dev-requester", "query": "close price", "purpose": "Synthetic purpose.",
    "element_keys": ["close_price"], "terms": [], "outcome": "reuse", "product_codes": ["demo_daily_close_prices"],
    "result": {"outcome": "reuse", "message": "This data is available in Daily close prices."},
}


@pytest.fixture
def repo(db_session, glossary):
    return ConsumerRequestRepository(db_session)


@pytest.fixture
def requirement(db_session, glossary):
    return RequirementRepository(db_session).create(
        requester_id="dev-requester", title="volume and esg score", element_keys=["volume"], terms=["esg score"]
    )


def raised(requirement) -> dict:
    return {**REUSE, "query": "volume and esg score", "element_keys": ["volume"], "terms": ["esg score"],
            "outcome": "raise_requirement", "product_codes": [], "requirement_id": requirement.id,
            "result": {"outcome": "raise_requirement"}}


def test_record_and_read_back(repo):
    request = repo.record(**REUSE)
    assert (request.requester_id, request.query, request.purpose) == ("dev-requester", "close price", "Synthetic purpose.")
    assert (request.element_keys, request.terms, request.product_codes) == (
        ["close_price"], [], ["demo_daily_close_prices"],
    )
    assert (request.outcome, request.requirement_id) == ("reuse", None)
    assert request.result == REUSE["result"]
    assert repo.get_for("dev-requester", request.id) == request


def test_a_raised_requirement_is_linked(repo, requirement):
    request = repo.record(**raised(requirement))
    assert (request.outcome, request.requirement_id) == ("raise_requirement", requirement.id)
    assert (request.element_keys, request.terms) == (["volume"], ["esg score"])


@pytest.mark.parametrize("outcome", ["reuse", "compose", "create_product"])
def test_every_other_outcome_is_recorded_without_a_requirement(repo, outcome):
    assert repo.record(**{**REUSE, "outcome": outcome, "result": {"outcome": outcome}}).outcome == outcome


def test_list_for_is_the_callers_own_newest_first(repo):
    first = repo.record(**REUSE)
    second = repo.record(**{**REUSE, "query": "bid price", "element_keys": ["bid_price"]})
    other = repo.record(**{**REUSE, "requester_id": "dev-someone-else"})
    assert [r.id for r in repo.list_for("dev-requester")] == [second.id, first.id]
    assert [r.id for r in repo.list_for("dev-someone-else")] == [other.id]
    assert repo.list_for("dev-nobody") == []


def test_another_requesters_request_is_not_found(repo):
    """The same named error as an unknown id, so the API cannot reveal that the request exists."""
    request = repo.record(**REUSE)
    with pytest.raises(ConsumerRequestNotFound):
        repo.get_for("dev-someone-else", request.id)
    with pytest.raises(ConsumerRequestNotFound):
        repo.get_for("dev-requester", uuid.uuid4())


def test_unknown_requirement_and_unknown_element(repo, requirement):
    with pytest.raises(RequirementNotFound):
        repo.record(**{**raised(requirement), "requirement_id": uuid.uuid4()})
    with pytest.raises(UnknownGlossaryElement, match="esg_score"):
        repo.record(**{**REUSE, "element_keys": ["esg_score"]})
    assert repo.list_for("dev-requester") == []


@pytest.mark.parametrize(
    "bad",
    [
        {"outcome": "found"},  # a step 17 outcome is not a routing outcome
        {"requester_id": " "},
        {"query": ""},
        {"element_keys": [], "terms": []},  # nothing was requested
        {"element_keys": ["Close Price"]},
        {"terms": [" "]},
        {"requirement_id": uuid.uuid4()},  # reuse never links a requirement
        {"outcome": "raise_requirement"},  # raise_requirement always links one
    ],
)
def test_validation(repo, bad):
    with pytest.raises(ValidationError):
        repo.record(**{**REUSE, **bad})


# ---- the database's own rules (raw SQL bypasses the repository on purpose)

RAW = (
    "INSERT INTO consumer_requests (requester_id, query, element_keys, terms, outcome, product_codes, "
    "requirement_id, result) VALUES (:requester_id, :query, :element_keys, :terms, :outcome, '{}', "
    ":requirement_id, '{}')"
)
RAW_REUSE = {"requester_id": "dev-requester", "query": "close price", "element_keys": ["close_price"], "terms": [],
             "outcome": "reuse", "requirement_id": None}


@pytest.mark.parametrize(
    ("bad", "constraint"),
    [
        ({"outcome": "found"}, "ck_consumer_requests_outcome"),
        ({"outcome": "raise_requirement"}, "ck_consumer_requests_requirement_matches_outcome"),
        ({"element_keys": []}, "ck_consumer_requests_something_requested"),
        ({"requester_id": " "}, "ck_consumer_requests_text_present"),
        ({"query": " "}, "ck_consumer_requests_text_present"),
    ],
)
def test_database_refuses_an_invalid_request(db_session, bad, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        db_session.execute(text(RAW), {**RAW_REUSE, **bad})


def test_database_refuses_a_requirement_on_another_outcome(db_session, requirement):
    with pytest.raises(IntegrityError, match="ck_consumer_requests_requirement_matches_outcome"):
        db_session.execute(text(RAW), {**RAW_REUSE, "requirement_id": requirement.id})
