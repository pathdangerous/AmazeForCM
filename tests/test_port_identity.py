"""Identity port, local adapter: seeded dev users, chosen with the X-Dev-User header."""

import json
from collections import Counter

import pytest
from pydantic import ValidationError

from ports.identity.base import ROLES, User
from ports.identity.dev import DEFAULT_USERS_FILE, DevIdentity


@pytest.fixture
def identity():
    return DevIdentity()


def test_exactly_one_user_per_role(identity):
    roles = Counter(role for user in identity.list_users() for role in user.roles)
    assert roles == Counter({role: 1 for role in ROLES})
    assert len(ROLES) == 7


def test_ids_unique_and_synthetic(identity):
    users = identity.list_users()
    assert len({u.id for u in users}) == len(users)
    assert all(u.email.endswith("@example.test") for u in users)
    assert all(u.id.startswith("dev-") for u in users)


def test_requester_is_consumer_others_are_backend(identity):
    assert [u.id for u in identity.list_users(app="consumer")] == ["dev-requester"]
    backend = identity.list_users(app="backend")
    assert len(backend) == 6
    assert "dev-requester" not in {u.id for u in backend}


def test_known_header_resolves(identity):
    user = identity.authenticate({"X-Dev-User": "dev-data-steward"})
    assert isinstance(user, User)
    assert user.roles == ["data_steward"]


def test_header_name_is_case_insensitive(identity):
    assert identity.authenticate({"x-dev-user": "dev-checker"}).id == "dev-checker"


@pytest.mark.parametrize("headers", [{}, {"X-Dev-User": ""}, {"X-Dev-User": "dev-nobody"}, {"Other": "dev-checker"}])
def test_unknown_or_missing_header_gives_no_user(identity, headers):
    assert identity.authenticate(headers) is None


def test_get_user(identity):
    assert identity.get_user("dev-data-owner").display_name == "Devon Owner"
    assert identity.get_user("dev-nobody") is None


def test_users_file_with_unknown_role_is_rejected(tmp_path):
    users = json.loads(DEFAULT_USERS_FILE.read_text(encoding="utf-8"))
    users[0]["roles"] = ["superuser"]
    bad = tmp_path / "users.json"
    bad.write_text(json.dumps(users), encoding="utf-8")
    with pytest.raises(ValidationError):
        DevIdentity(bad)
