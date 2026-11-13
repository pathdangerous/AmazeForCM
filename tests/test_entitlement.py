"""The entitlement decision (core/entitlement/decide.py): rules only, no model call (CLAUDE.md rule 2).

Decision 4 of the phase 2 plan: a product is open or restricted; grant and revoke events are per user
or per role; the latest event wins; a user's own event beats one for their role; the owner always has
access. Each test below names the line of the rule it proves (plan, assumption 18).
"""

import uuid
from datetime import UTC, datetime

import pytest

from core.domain.entitlement import Access, GrantEvent
from core.entitlement.decide import decide, route_to_access

PRODUCT_ID = uuid.uuid4()


def event(seq: int, subject_type: str, subject: str, action: str) -> GrantEvent:
    return GrantEvent(id=uuid.uuid4(), created_at=datetime.now(UTC), seq=seq, product_id=PRODUCT_ID,
                      subject_type=subject_type, subject=subject, action=action, decided_by="dev-data-owner")


def access(events=(), *, visibility="restricted", user_id="dev-requester", roles=("requester",)) -> Access:
    return decide(visibility=visibility, owner_id="dev-data-owner", user_id=user_id, roles=roles, events=events)


def test_open_product_ignores_grant_events():
    revoked = [event(1, "user", "dev-requester", "revoke"), event(2, "role", "requester", "revoke")]
    assert access(visibility="open") == Access(entitled=True, reason="open")
    assert access(revoked, visibility="open") == Access(entitled=True, reason="open")


def test_owner_always_has_access():
    revoked = [event(1, "user", "dev-data-owner", "revoke"), event(2, "role", "data_owner", "revoke")]
    assert access(revoked, user_id="dev-data-owner", roles=("data_owner",)) == Access(entitled=True, reason="owner")


def test_latest_user_event_wins():
    grant_then_revoke = [event(1, "user", "dev-requester", "grant"), event(2, "user", "dev-requester", "revoke")]
    revoke_then_grant = [event(1, "user", "dev-requester", "revoke"), event(2, "user", "dev-requester", "grant")]
    assert access(grant_then_revoke) == Access(entitled=False, reason="user_revoke")
    assert access(revoke_then_grant) == Access(entitled=True, reason="user_grant")
    # seq decides, not the order of the list
    assert access(list(reversed(grant_then_revoke))) == Access(entitled=False, reason="user_revoke")


def test_user_event_beats_role_event():
    """Even when the role's event is the later one."""
    user_revoked = [event(1, "user", "dev-requester", "revoke"), event(2, "role", "requester", "grant")]
    user_granted = [event(1, "user", "dev-requester", "grant"), event(2, "role", "requester", "revoke")]
    assert access(user_revoked) == Access(entitled=False, reason="user_revoke")
    assert access(user_granted) == Access(entitled=True, reason="user_grant")


def test_latest_role_event_wins():
    grant = [event(1, "role", "requester", "grant")]
    grant_then_revoke = [*grant, event(2, "role", "requester", "revoke")]
    revoke_then_grant = [event(1, "role", "requester", "revoke"), event(2, "role", "requester", "grant")]
    assert access(grant) == Access(entitled=True, reason="role_grant")
    assert access(grant_then_revoke) == Access(entitled=False, reason="no_grant")
    assert access(revoke_then_grant) == Access(entitled=True, reason="role_grant")


def test_any_granted_role_is_enough():
    """A user with two roles keeps access through one when the other is revoked later."""
    events = [event(1, "role", "checker", "grant"), event(2, "role", "data_steward", "revoke")]
    assert access(events, user_id="dev-two-roles", roles=("data_steward", "checker")) == Access(
        entitled=True, reason="role_grant"
    )


def test_no_event_is_not_entitled():
    others = [event(1, "user", "dev-someone-else", "grant"), event(2, "role", "operations", "grant")]
    assert access() == Access(entitled=False, reason="no_grant")
    assert access(others) == Access(entitled=False, reason="no_grant")


def test_a_user_id_is_never_read_as_a_role():
    """A grant to the role named 'requester' does not reach a user whose id is 'requester', and back."""
    assert access([event(1, "role", "requester", "grant")], user_id="requester", roles=("operations",)).entitled is False
    assert access([event(1, "user", "requester", "grant")], user_id="dev-requester", roles=("requester",)).entitled is False


@pytest.mark.parametrize("visibility", ["restricted", "secret", ""])
def test_anything_but_open_is_treated_as_restricted(visibility):
    assert access(visibility=visibility) == Access(entitled=False, reason="no_grant")


def test_route_text():
    assert route_to_access("Devon Owner", "Bond reference data") == "Ask Devon Owner for access to Bond reference data"
