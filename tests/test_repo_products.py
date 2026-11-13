"""ProductRepository: data products end users find and reuse (steps 17-22)."""

import uuid

import pytest
from pydantic import ValidationError

from core.repositories.errors import DuplicateProductCode, ProductNotFound, RequirementNotFound
from core.repositories.products import ProductRepository
from core.repositories.requirements import RequirementRepository


@pytest.fixture
def repo(db_session):
    return ProductRepository(db_session)


def test_create_and_find(repo, db_session):
    requirement = RequirementRepository(db_session).create(requester_id="dev-requester", title="Synthetic")
    product = repo.create(code="synthetic_bond_prices", name="Synthetic bond prices", domain="pricing",
                          description="Synthetic product for tests.", requirement_id=requirement.id)
    assert repo.get(product.id) == product
    assert repo.get_by_code("synthetic_bond_prices") == product


def test_list_by_domain_sorted_by_name(repo):
    repo.create(code="b_prices", name="B prices", domain="pricing")
    repo.create(code="a_prices", name="A prices", domain="pricing")
    repo.create(code="instruments", name="Instruments", domain="security_master")
    assert [p.code for p in repo.list(domain="pricing")] == ["a_prices", "b_prices"]
    assert len(repo.list()) == 3


def test_duplicate_code(repo):
    repo.create(code="p", name="P", domain="pricing")
    with pytest.raises(DuplicateProductCode, match="'p'"):
        repo.create(code="p", name="Another", domain="pricing")


def test_unknown_requirement_and_product(repo):
    with pytest.raises(RequirementNotFound):
        repo.create(code="p", name="P", domain="pricing", requirement_id=uuid.uuid4())
    with pytest.raises(ProductNotFound):
        repo.get(uuid.uuid4())
    with pytest.raises(ProductNotFound):
        repo.get_by_code("nope")


@pytest.mark.parametrize("bad", [{"code": "P 1"}, {"name": ""}, {"domain": "Pricing"}])
def test_validation(repo, bad):
    with pytest.raises(ValidationError):
        repo.create(**{"code": "p", "name": "P", "domain": "pricing", **bad})
