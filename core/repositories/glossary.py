"""GlossaryRepository: versioned glossary elements.

- add_seed_element: version 1 of a key that does not exist yet, created_by "seed", no proposal.
- add_version: the next version of a key (or version 1 of a new key), only from an approved
  glossary_element proposal (rule 1). Nothing is ever updated or deleted (rule 7).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from core.domain.glossary import GlossaryElement, GlossaryElementDraft
from core.repositories.errors import UnknownGlossaryElement
from core.repositories.proposals import ProposalRepository
from core.repositories.tables import GlossaryElementRow as Row

SEED = "seed"


class GlossaryRepository:
    def __init__(self, session: Session):
        self._session = session

    # ---- reading

    def list_current(self, domain: str | None = None, q: str | None = None) -> list[GlossaryElement]:
        current = self._current().subquery()
        element = select(Row).join(current, Row.id == current.c.id)
        if domain:
            element = element.where(Row.domain == domain)
        if q and q.strip():
            pattern = f"%{q.strip()}%"
            element = element.where(
                or_(
                    Row.key.ilike(pattern),
                    Row.name.ilike(pattern),
                    Row.definition.ilike(pattern),
                    func.array_to_string(Row.aliases, " ").ilike(pattern),
                )
            )
        return [GlossaryElement.model_validate(r) for r in self._session.scalars(element.order_by(Row.key))]

    def get_current(self, key: str) -> GlossaryElement:
        row = self._session.scalars(self._current().where(Row.key == key)).one_or_none()
        if row is None:
            raise UnknownGlossaryElement(f"No glossary element with key {key!r}")
        return GlossaryElement.model_validate(row)

    def exists(self, key: str) -> bool:
        return self._session.scalar(select(func.count()).select_from(Row).where(Row.key == key)) > 0

    def require_keys(self, keys: Iterable[str]) -> None:
        """Raise UnknownGlossaryElement, naming every key that has no element. One query."""
        keys = list(keys)
        if not keys:
            return
        known = set(self._session.scalars(select(Row.key).where(Row.key.in_(keys))))
        unknown = [key for key in keys if key not in known]
        if unknown:
            raise UnknownGlossaryElement(f"No glossary element with key {', '.join(map(repr, unknown))}")

    def history(self, key: str) -> list[GlossaryElement]:
        rows = self._session.scalars(select(Row).where(Row.key == key).order_by(Row.version)).all()
        if not rows:
            raise UnknownGlossaryElement(f"No glossary element with key {key!r}")
        return [GlossaryElement.model_validate(r) for r in rows]

    def domains(self) -> list[tuple[str, int]]:
        """Each domain with the number of current elements in it, by domain name."""
        current = self._current().subquery()
        rows = self._session.execute(
            select(current.c.domain, func.count()).group_by(current.c.domain).order_by(current.c.domain)
        )
        return [(domain, count) for domain, count in rows]

    # ---- writing (insert only)

    def add_seed_element(self, **fields: Any) -> bool:
        """Insert version 1 with created_by "seed" if the key is new. Returns False if it already exists."""
        draft = GlossaryElementDraft(**fields)
        if self.exists(draft.key):
            return False
        self._insert(draft, version=1, created_by=SEED, proposal_id=None)
        return True

    def add_version(self, *, proposal_id: UUID, created_by: str, **fields: Any) -> GlossaryElement:
        draft = GlossaryElementDraft(**fields)
        if not created_by.strip() or created_by == SEED:
            raise ValueError("created_by must name the person, and cannot be 'seed'")
        ProposalRepository(self._session).require_approved(proposal_id, kind="glossary_element")
        latest = self._session.scalar(select(func.max(Row.version)).where(Row.key == draft.key))
        return self._insert(draft, version=(latest or 0) + 1, created_by=created_by, proposal_id=proposal_id)

    # ---- helpers

    def _insert(self, draft: GlossaryElementDraft, *, version: int, created_by: str,
                proposal_id: UUID | None) -> GlossaryElement:
        row = Row(**draft.model_dump(), version=version, created_by=created_by, proposal_id=proposal_id)
        self._session.add(row)
        self._session.flush()
        return GlossaryElement.model_validate(row)

    @staticmethod
    def _current() -> Select[tuple[Row]]:
        """The highest version of every key (PostgreSQL DISTINCT ON)."""
        return select(Row).ext(distinct_on(Row.key)).order_by(Row.key, Row.version.desc())
