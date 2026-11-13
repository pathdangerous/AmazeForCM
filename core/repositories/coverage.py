"""DomainCoverage: which glossary elements Amaze already holds ("in the domains").

An element is in the domains when some feed is registered, has at least one released load, and its
current mapping (the highest version of a source field) points at the element. Every mapping row comes
from an approved proposal: the database refuses any other (migration 0005).

Read only. The routing checks use it for the create_product outcome.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from core.repositories.tables import FeedRow, LoadRow, MappingRow


class DomainCoverage:
    def __init__(self, session: Session):
        self._session = session

    def in_domains(self, element_keys: Iterable[str]) -> set[str]:
        """The subset of element_keys that is in the domains."""
        keys = list(element_keys)
        if not keys:
            return set()
        current = (  # the highest version of every source field of every feed
            select(MappingRow.feed_id, MappingRow.element_key)
            .ext(distinct_on(MappingRow.feed_id, MappingRow.source_field))
            .order_by(MappingRow.feed_id, MappingRow.source_field, MappingRow.version.desc())
            .subquery()
        )
        has_released_load = exists().where(LoadRow.feed_id == FeedRow.id, LoadRow.status == "released")
        query = (
            select(current.c.element_key)
            .join(FeedRow, FeedRow.id == current.c.feed_id)
            .where(FeedRow.stage == "registered", has_released_load, current.c.element_key.in_(keys))
            .distinct()
        )
        return set(self._session.scalars(query))
