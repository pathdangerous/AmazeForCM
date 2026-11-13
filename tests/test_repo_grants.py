"""ProductGrantRepository: grant and revoke events on a product, per user or per role (decision 4).

The table is append-only. The order of events is the seq identity column, so "the latest event" is
never a tie, even for events written in one transaction.
"""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from core.entitlement.decide import decide
from core.repositories.errors import ProductNotFound
from core.repositories.grants import ProductGrantRepository

GRANT = {"subject_type": "user", "subject": "dev-requester", "action": "grant", "decided_by": "dev-data-owner"}


@pytest.fixture
def repo(db_session):
    return ProductGrantRepository(db_session)


@pytest.fixture
def product(make_product):
    return make_product("restricted_prices", visibility="restricted")


def test_record_and_read_back(repo, product):
    event = repo.record(product_id=product.id, **GRANT)
    assert (event.product_id, event.subject_type, event.subject, event.action, event.decided_by) == (
        product.id, "user", "dev-requester", "grant", "dev-data-owner",
    )
    assert repo.events_for(product.id) == [event]


def test_events_are_append_only_and_ordered_by_seq(repo, product, db_session):
    first = repo.record(product_id=product.id, **GRANT)
    second = repo.record(product_id=product.id, **{**GRANT, "action": "revoke"})
    third = repo.record(product_id=product.id, **{**GRANT, "subject_type": "role", "subject": "requester"})
    events = repo.events_for(product.id)
    assert [e.id for e in events] == [first.id, second.id, third.id]
    assert first.seq < second.seq < third.seq
    with pytest.raises(DBAPIError, match="product_grants is append-only"):
        db_session.execute(text("UPDATE product_grants SET action = 'grant' WHERE id = :id"), {"id": second.id})


def test_events_of_another_product_are_not_returned(repo, product, make_product):
    other = make_product("other_prices", visibility="restricted")
    repo.record(product_id=other.id, **GRANT)
    assert repo.events_for(product.id) == []


def test_events_by_product_reads_many_products_at_once(repo, product, make_product):
    other = make_product("other_prices", visibility="restricted")
    quiet = make_product("quiet_prices", visibility="restricted")
    first = repo.record(product_id=product.id, **GRANT)
    second = repo.record(product_id=other.id, **GRANT)
    third = repo.record(product_id=product.id, **{**GRANT, "action": "revoke"})
    events = repo.events_by_product([product.id, other.id, quiet.id])
    assert {k: [e.id for e in v] for k, v in events.items()} == {product.id: [first.id, third.id], other.id: [second.id]}
    assert repo.events_by_product([]) == {}


def test_unknown_product(repo):
    with pytest.raises(ProductNotFound):
        repo.record(product_id=uuid.uuid4(), **GRANT)
    with pytest.raises(ProductNotFound):
        repo.events_for(uuid.uuid4())


@pytest.mark.parametrize(
    "bad",
    [
        {"subject_type": "group"},
        {"action": "deny"},
        {"subject": " "},
        {"decided_by": ""},
        {"subject_type": "role", "subject": "superuser"},  # a role subject is one of the seven roles
        {"subject_type": "role", "subject": "dev-requester"},
    ],
)
def test_validation(repo, product, bad):
    with pytest.raises(ValidationError):
        repo.record(product_id=product.id, **{**GRANT, **bad})


def test_the_decision_reads_stored_events(repo, product):
    """The repository's events are what core.entitlement decides on: grant, then revoke."""

    def entitled() -> bool:
        return decide(visibility=product.visibility, owner_id=product.owner_id, user_id="dev-requester",
                      roles=["requester"], events=repo.events_for(product.id)).entitled

    assert entitled() is False
    repo.record(product_id=product.id, **GRANT)
    assert entitled() is True
    repo.record(product_id=product.id, **{**GRANT, "action": "revoke"})
    assert entitled() is False


# ---- the database's own rules (raw SQL bypasses the repository on purpose)

RAW = (
    "INSERT INTO product_grants (product_id, subject_type, subject, action, decided_by) "
    "VALUES (:product, :subject_type, :subject, :action, :decided_by)"
)


@pytest.mark.parametrize(
    ("bad", "constraint"),
    [
        ({"subject_type": "group"}, "ck_product_grants_subject_type"),
        ({"action": "deny"}, "ck_product_grants_action"),
        ({"subject_type": "role", "subject": "superuser"}, "ck_product_grants_known_role"),
        ({"subject": " "}, "ck_product_grants_text_present"),
        ({"decided_by": " "}, "ck_product_grants_text_present"),
    ],
)
def test_database_refuses_an_invalid_event(db_session, product, bad, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        db_session.execute(text(RAW), {"product": product.id, **GRANT, **bad})


def test_database_refuses_an_event_for_an_unknown_product(db_session):
    with pytest.raises(IntegrityError, match="fk_product_grants_product_id_products"):
        db_session.execute(text(RAW), {"product": uuid.uuid4(), **GRANT})


def test_seq_cannot_be_set_by_hand(db_session, product):
    """seq is GENERATED ALWAYS, so nobody can write an event that sorts before an existing one."""
    with pytest.raises(DBAPIError, match="seq"):
        db_session.execute(
            text("INSERT INTO product_grants (seq, product_id, subject_type, subject, action, decided_by) "
                 "VALUES (0, :product, 'user', 'dev-requester', 'grant', 'dev-data-owner')"),
            {"product": product.id},
        )
