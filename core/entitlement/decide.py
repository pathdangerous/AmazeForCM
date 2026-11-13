"""The entitlement decision. Rules only: no model call, no database access, no ports (CLAUDE.md rule 2).

In order (phase 2 plan, assumption 18):
1. an open product is entitled, whatever its grant events say;
2. the owner is entitled;
3. if the user's own id has grant or revoke events, the latest one decides;
4. otherwise, entitled if the latest event for any one of the user's roles is a grant;
5. otherwise not entitled.

"Latest" is the highest seq. Anything that is not open is treated as restricted.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from core.domain.entitlement import Access, GrantEvent


def _latest(events: Iterable[GrantEvent], subject_type: str, subject: str) -> GrantEvent | None:
    own = [e for e in events if e.subject_type == subject_type and e.subject == subject]
    return max(own, key=lambda e: e.seq) if own else None


def decide(*, visibility: str, owner_id: str, user_id: str, roles: Sequence[str],
           events: Sequence[GrantEvent]) -> Access:
    """events: the grant and revoke events of this one product, in any order."""
    if visibility == "open":
        return Access(entitled=True, reason="open")
    if user_id == owner_id:
        return Access(entitled=True, reason="owner")
    own = _latest(events, "user", user_id)
    if own is not None:
        granted = own.action == "grant"
        return Access(entitled=granted, reason="user_grant" if granted else "user_revoke")
    for role in roles:
        latest = _latest(events, "role", role)
        if latest is not None and latest.action == "grant":
            return Access(entitled=True, reason="role_grant")
    return Access(entitled=False, reason="no_grant")


def route_to_access(owner_name: str, product_name: str) -> str:
    """The route shown with a restricted product. Text only: there is no access-request workflow."""
    return f"Ask {owner_name} for access to {product_name}"
