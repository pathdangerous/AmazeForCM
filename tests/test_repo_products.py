"""ProductRepository: data products end users find and reuse (steps 17-22).

Phase 2: a product has an owner, a visibility (open or restricted) and at least one glossary element.
Its element list is fixed when the product is created: product_elements is append-only, and the
database refuses an element_key that is not in the glossary even when the repository is bypassed.
"""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from core.repositories.errors import (
    DuplicateProductCode,
    ProductNotFound,
    RequirementNotFound,
    UnknownGlossaryElement,
)
from core.repositories.products import ProductRepository
from core.repositories.requirements import RequirementRepository

PRODUCT = {"code": "p", "name": "P", "domain": "pricing", "owner_id": "dev-data-owner", "visibility": "open",
           "element_keys": ["close_price"]}


@pytest.fixture
def repo(db_session, glossary):
    return ProductRepository(db_session)


def test_create_and_find(repo, db_session):
    requirement = RequirementRepository(db_session).create(requester_id="dev-requester", title="Synthetic")
    product = repo.create(code="synthetic_bond_prices", name="Synthetic bond prices", domain="pricing",
                          description="Synthetic product for tests.", requirement_id=requirement.id,
                          owner_id="dev-data-owner", visibility="restricted",
                          element_keys=["price_date", "close_price"])
    assert (product.owner_id, product.visibility) == ("dev-data-owner", "restricted")
    assert repo.get(product.id) == product
    assert repo.get_by_code("synthetic_bond_prices") == product
    assert repo.elements_of(product.id) == ["close_price", "price_date"]


def test_list_by_domain_sorted_by_name(repo):
    repo.create(**{**PRODUCT, "code": "b_prices", "name": "B prices"})
    repo.create(**{**PRODUCT, "code": "a_prices", "name": "A prices"})
    repo.create(**{**PRODUCT, "code": "instruments", "name": "Instruments", "domain": "security_master"})
    assert [p.code for p in repo.list(domain="pricing")] == ["a_prices", "b_prices"]
    assert len(repo.list()) == 3


def test_list_with_elements(repo):
    repo.create(**{**PRODUCT, "code": "b_prices", "name": "B prices", "element_keys": ["close_price", "bid_price"]})
    repo.create(**{**PRODUCT, "code": "a_prices", "name": "A prices"})
    repo.create(**{**PRODUCT, "code": "instruments", "name": "Instruments", "domain": "security_master",
                   "visibility": "restricted", "element_keys": ["isin"]})
    listed = repo.list_with_elements()
    assert [(p.code, p.visibility, p.element_keys) for p in listed] == [
        ("a_prices", "open", ["close_price"]),
        ("b_prices", "open", ["bid_price", "close_price"]),
        ("instruments", "restricted", ["isin"]),
    ]
    assert [p.code for p in repo.list_with_elements(domain="pricing")] == ["a_prices", "b_prices"]


def test_duplicate_code(repo):
    repo.create(**PRODUCT)
    with pytest.raises(DuplicateProductCode, match="'p'"):
        repo.create(**{**PRODUCT, "name": "Another"})


def test_unknown_requirement_and_product(repo):
    with pytest.raises(RequirementNotFound):
        repo.create(**PRODUCT, requirement_id=uuid.uuid4())
    with pytest.raises(ProductNotFound):
        repo.get(uuid.uuid4())
    with pytest.raises(ProductNotFound):
        repo.get_by_code("nope")
    with pytest.raises(ProductNotFound):
        repo.elements_of(uuid.uuid4())


def test_unknown_element_is_refused_and_nothing_is_created(repo):
    with pytest.raises(UnknownGlossaryElement, match="esg_score"):
        repo.create(**{**PRODUCT, "element_keys": ["close_price", "esg_score"]})
    assert repo.list() == []


@pytest.mark.parametrize(
    "bad",
    [
        {"code": "P 1"},
        {"name": ""},
        {"domain": "Pricing"},
        {"owner_id": " "},
        {"visibility": "secret"},
        {"element_keys": []},  # a product holds at least one element
        {"element_keys": ["close_price", "close_price"]},
        {"element_keys": ["Close Price"]},
    ],
)
def test_validation(repo, bad):
    with pytest.raises(ValidationError):
        repo.create(**{**PRODUCT, **bad})


@pytest.mark.parametrize("missing", ["owner_id", "visibility", "element_keys"])
def test_owner_visibility_and_elements_are_required(repo, missing):
    with pytest.raises(ValidationError):
        repo.create(**{k: v for k, v in PRODUCT.items() if k != missing})


# ---- the database's own rules (raw SQL bypasses the repository on purpose)

RAW_PRODUCT = (
    "INSERT INTO products (code, name, domain, owner_id, visibility) "
    "VALUES ('raw_p', 'Raw', 'pricing', :owner_id, :visibility)"
)


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"owner_id": "dev-data-owner", "visibility": "secret"}, "ck_products_visibility"),
        ({"owner_id": " ", "visibility": "open"}, "ck_products_owner_present"),
        ({"owner_id": None, "visibility": "open"}, "owner_id"),
        ({"owner_id": "dev-data-owner", "visibility": None}, "visibility"),
    ],
)
def test_database_refuses_a_product_without_a_valid_owner_and_visibility(db_session, values, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        db_session.execute(text(RAW_PRODUCT), values)


def test_database_refuses_an_element_key_that_is_not_in_the_glossary(repo, db_session):
    product = repo.create(**PRODUCT)
    with pytest.raises(DBAPIError, match="element_key esg_score is not in the glossary"):
        db_session.execute(
            text("INSERT INTO product_elements (product_id, element_key) VALUES (:p, 'esg_score')"), {"p": product.id}
        )


def test_database_accepts_a_known_element_key(repo, db_session):
    product = repo.create(**PRODUCT)
    db_session.execute(
        text("INSERT INTO product_elements (product_id, element_key) VALUES (:p, 'bid_price')"), {"p": product.id}
    )
    assert repo.elements_of(product.id) == ["bid_price", "close_price"]


def test_database_refuses_the_same_element_twice(repo, db_session):
    product = repo.create(**PRODUCT)
    with pytest.raises(IntegrityError, match="uq_product_elements_product_id_element_key"):
        db_session.execute(
            text("INSERT INTO product_elements (product_id, element_key) VALUES (:p, 'close_price')"),
            {"p": product.id},
        )


# ---------------------------------------------------------------- phase 7: a product created from a feed (0019, G15)


def _registration(db_session, feed_id):
    return db_session.execute(text("SELECT owner_id, visibility FROM feed_registrations WHERE feed_id = :f "
                                   "ORDER BY version DESC LIMIT 1"), {"f": feed_id}).one()


def test_a_product_of_a_released_feed_is_found_by_its_feed(repo, db_session):
    from tests.configured import a_registered_feed, released_load

    feed = a_registered_feed(db_session, "syn_product_feed")
    released_load(db_session, feed)
    owner, visibility = _registration(db_session, feed)
    assert repo.for_feed(feed) is None
    product = repo.create(**{**PRODUCT, "code": "syn_product_feed", "owner_id": owner, "visibility": visibility,
                             "feed_id": feed})
    assert product.feed_id == feed and repo.for_feed(feed) == product
    assert repo.get(product.id).feed_id == feed
    assert repo.create(**{**PRODUCT, "code": "no_feed"}).feed_id is None  # a hand-made product has none


def test_a_product_of_a_feed_without_a_released_load_is_refused(repo, db_session):
    from tests.configured import a_registered_feed

    feed = a_registered_feed(db_session, "syn_product_unreleased")
    owner, visibility = _registration(db_session, feed)
    with pytest.raises(DBAPIError, match="has no released load"):
        with db_session.begin_nested():
            repo.create(**{**PRODUCT, "owner_id": owner, "visibility": visibility, "feed_id": feed})
