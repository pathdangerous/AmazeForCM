"""ExceptionResolutionRepository (phase 7.14): one approved exception fix of a held load's group, recorded. Append-only;
trg_exception_resolutions_a_guard (migration 0034) lets a row in only for an approved exception_fix of the feed whose
every approver step is done, matching it. The latest row per feed, rule and pattern is the current one."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.domain.proposal import Proposal
from core.repositories.tables import ExceptionResolutionRow as Row


class ExceptionResolutionRepository:
    def __init__(self, session: Session):
        self._session = session

    def record(self, proposal: Proposal, *, decided_by: str) -> Row:
        """The resolution of an approved exception_fix, from its payload."""
        p = proposal.payload
        row = Row(feed_id=proposal.feed_id, load_id=UUID(p["load_id"]), rule_key=p["rule_key"],
                  element_keys=list(p["element_keys"]), pattern=p["pattern"], group_key=p["group_key"],
                  action={"waive": "waived", "refer": "referred"}[p["action"]], reason=p["reason"],
                  proposal_id=proposal.id, decided_by=decided_by,
                  applied_by="agent" if proposal.origin == "agent" else "manual")
        self._session.add(row)
        self._session.flush()
        return row

    def list_for_feed(self, feed_id: UUID) -> list[Row]:
        """Oldest first."""
        return list(self._session.scalars(select(Row).where(Row.feed_id == feed_id)
                                          .order_by(Row.created_at, Row.id)))

    def current_waivers(self, feed_id: UUID) -> list[Row]:
        """The waivers no later resolution of the same rule and pattern ended (a referral ends one)."""
        latest: dict[tuple[str, str], Row] = {}
        for row in self.list_for_feed(feed_id):
            latest[(row.rule_key, row.pattern)] = row
        return [r for r in latest.values() if r.action == "waived"]
