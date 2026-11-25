"""GlossaryRepository: versioned glossary elements.

- add_seed_element: version 1 of a key that does not exist yet, created_by "seed", no proposal.
- add_version: one option of an approved glossary_element proposal (rule 1): a new element at version 1,
  or an alias as the next version of its key (phase 5). Nothing is ever updated or deleted (rule 7).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from core.domain.glossary import GlossaryElement, GlossaryElementDraft
from core.domain.mapping import ElementOption
from core.repositories.errors import AliasAlreadyOnElement, NotAProposalOption, PhraseTaken, UnknownGlossaryElement
from core.repositories.proposals import ProposalRepository
from core.repositories.tables import GlossaryElementRow as Row
from core.steps.matching import words

SEED = "seed"
GLOSSARY_LOCK = "amaze:glossary"


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

    def lock(self) -> None:
        """The advisory lock on the glossary, held until the transaction ends. Last in the lock order (phase 5
        plan, assumption 49); the next element version and phrases_taken() are read after it. The key includes
        the schema (phase 7 plan, assumption 2a)."""
        key = func.concat(func.current_schema(), ":", GLOSSARY_LOCK)
        self._session.execute(select(func.pg_advisory_xact_lock(func.hashtext(key))))

    def phrases_taken(self, draft: GlossaryElementDraft) -> list[tuple[str, str]]:
        """Each (phrase, owner key) where another current element owns a key, name or alias of draft, by
        words (matching.words), so "Close price" and close_price are one phrase."""
        wanted = {words(p): p for p in (draft.key, draft.name, *draft.aliases)}
        taken = []
        for element in self.list_current():
            if element.key == draft.key:
                continue
            for p in (element.key, element.name, *element.aliases):
                if words(p) in wanted:
                    taken.append((wanted[words(p)], element.key))
        return sorted(set(taken))

    def add_version(self, *, proposal_id: UUID, created_by: str, option: ElementOption) -> GlossaryElement:
        """Write one option of an approved glossary_element proposal (phase 5 plan, assumption 17): a new
        element at version 1, or an alias as the key's next version, equal to the last but for the alias.
        The caller holds lock() and has checked phrases_taken()."""
        if not created_by.strip() or created_by == SEED:
            raise ValueError("created_by must name the person, and cannot be 'seed'")
        proposal = ProposalRepository(self._session).require_approved(proposal_id, kind="glossary_element")
        if option.model_dump(mode="json") not in proposal.payload.get("options", []):
            raise NotAProposalOption(f"Proposal {proposal_id} does not offer {option.kind} {option.key}")
        if option.kind == "new_element":
            if self.exists(option.key):
                raise PhraseTaken(f"The key {option.key!r} is already a glossary element")
            return self._insert(option.element, version=1, created_by=created_by, proposal_id=proposal_id)
        last = self.get_current(option.key)
        if words(option.alias) in {words(p) for p in (last.key, last.name, *last.aliases)}:
            raise AliasAlreadyOnElement(f"{last.key} (version {last.version}) already has the name {option.alias}")
        draft = GlossaryElementDraft(key=last.key, name=last.name, domain=last.domain, data_type=last.data_type,
                                     definition=last.definition, aliases=[*last.aliases, option.alias], unit=last.unit)
        return self._insert(draft, version=last.version + 1, created_by=created_by, proposal_id=proposal_id)

    def add_import(self, *, proposal_id: UUID, created_by: str) -> GlossaryElement:
        """Write the element of an approved glossary_import proposal (phase 7.19): version 1 of a new key, or the key's
        next version (rule 7: never an edit in place). The caller holds lock(), has checked the key and phrases_taken()
        and records the approval first; the database checks the row against the proposal again."""
        if not created_by.strip() or created_by == SEED:
            raise ValueError("created_by must name the person, and cannot be 'seed'")
        proposal = ProposalRepository(self._session).require_approved(proposal_id, kind="glossary_import")
        draft = GlossaryElementDraft(**proposal.payload["element"])
        if proposal.payload["label"] == "new":
            if self.exists(draft.key):
                raise PhraseTaken(f"The key {draft.key!r} is already a glossary element")
            return self._insert(draft, version=1, created_by=created_by, proposal_id=proposal_id)
        return self._insert(draft, version=self.get_current(draft.key).version + 1, created_by=created_by,
                            proposal_id=proposal_id)

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
