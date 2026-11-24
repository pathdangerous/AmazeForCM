"""The draft configuration of a feed (phase 7.13, plan decisions 1 and 2). Read only: it never writes.

draft_config() is the feed's approved configuration plus its pending proposals taken as if approved, so that an agent
can draft on the drafts before it (glossary -> mapping -> transformations -> rules). Each part keeps the proposal it
came from, so a draft can record its bases (ProposalRepository.add_bases) and check_unchanged() can tell, under the
feed lock in before_write, that none of them was edited or rejected while the model answered.

- A pending glossary_element with a recommended option: a new element, or an alias on an approved one.
  A conflict (no recommended option) adds nothing: its column is blocked.
- A pending mapping with a recommended element: that column's mapping (a draft `Mapping`, version 0, id = the
  proposal's id). A tie is blocked; a column left out (`excluded`) is not mapped.
- A pending transformation: that column's steps (a draft `Transformation`, version 0).

Nothing here touches `mappings`, `rules` or `transformations`: those rows are written only by an approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from core.domain.configuration import Mapping
from core.domain.mapping import ElementOption
from core.domain.transformation import Transformation
from core.repositories.configuration import MappingRepository
from core.repositories.errors import ConfigurationChangedMeanwhile
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.transformations import TransformationRepository

DRAFT = "draft"  # created_by of a draft Transformation: nobody stored it


@dataclass(frozen=True)
class DraftElement:
    """A glossary element a pending proposal would create (candidates.Element)."""

    key: str
    name: str
    data_type: str
    definition: str
    aliases: list[str]


@dataclass(frozen=True)
class DraftConfig:
    feed_id: UUID
    elements: list  # approved GlossaryElement and DraftElement, for candidate_list()
    element_bases: dict[str, UUID]  # element key -> the glossary draft that creates it or adds an alias
    mappings: list[Mapping]  # approved, and drafts (version 0)
    mapping_bases: dict[str, UUID]  # column -> its mapping draft
    transformations: list[Transformation]
    transformation_bases: dict[str, UUID]  # column -> its transformation draft
    types: dict[str, str]  # element key -> data type
    blocked: dict[str, str] = field(default_factory=dict)  # column -> why no single value can be drafted past
    known_mapping_proposals: frozenset[UUID] = frozenset()  # every mapping, approved or draft, by its proposal

    @property
    def used(self) -> set[UUID]:
        return {*self.element_bases.values(), *self.mapping_bases.values(), *self.transformation_bases.values()}

    def bases_for_candidates(self, candidates: list[str]) -> list[UUID]:
        return [self.element_bases[k] for k in candidates if k in self.element_bases]

    def bases_for_columns(self, columns: list[str]) -> list[UUID]:
        return [b for c in columns for b in (self.mapping_bases.get(c), self.transformation_bases.get(c)) if b]


def _with_option(elements: dict[str, object], option: ElementOption) -> object | None:
    if option.kind == "new_element":
        e = option.element
        return DraftElement(key=e.key, name=e.name, data_type=e.data_type, definition=e.definition,
                            aliases=list(e.aliases))
    existing = elements.get(option.key)
    if existing is None:
        return None
    return DraftElement(key=existing.key, name=existing.name, data_type=existing.data_type,
                        definition=existing.definition, aliases=[*existing.aliases, option.alias])


def draft_config(session: Session, feed_id: UUID) -> DraftConfig:
    proposals = ProposalRepository(session)
    elements = {e.key: e for e in GlossaryRepository(session).list_current()}
    element_bases: dict[str, UUID] = {}
    blocked: dict[str, str] = {}
    for p in proposals.list_for_feed(feed_id, "glossary_element", "pending"):
        if p.payload.get("recommended") is None:
            blocked[p.payload["source_field"]] = "a glossary conflict for the glossary owner"
            continue
        made = _with_option(elements, ElementOption(**p.payload["options"][p.payload["recommended"]]))
        if made is not None:
            elements[made.key], element_bases[made.key] = made, p.id

    mappings = {m.source_field: m for m in MappingRepository(session).current_for_feed(feed_id)}
    known = {m.proposal_id for m in mappings.values()}
    mapping_bases: dict[str, UUID] = {}
    for p in proposals.list_for_feed(feed_id, "mapping", "pending"):
        column = p.payload["source_field"]
        if p.payload.get("excluded"):
            mappings.pop(column, None)
            continue
        if p.payload.get("recommended") is None:
            blocked[column] = "a mapping tie for the data steward"
            continue
        mappings[column] = Mapping(id=p.id, created_at=p.created_at, feed_id=feed_id, source_field=column,
                                   element_key=p.payload["recommended"], version=0, proposal_id=p.id)
        mapping_bases[column] = p.id

    transformations = {t.source_field: t for t in TransformationRepository(session).current_for_feed(feed_id)}
    transformation_bases: dict[str, UUID] = {}
    for p in proposals.list_for_feed(feed_id, "transformation", "pending"):
        column = p.payload["source_field"]
        if column not in mappings:
            continue
        transformations[column] = Transformation(id=p.id, version=0, feed_id=feed_id, source_field=column,
                                                 steps=p.payload["steps"], proposal_id=p.id, created_by=DRAFT,
                                                 created_at=p.created_at)
        transformation_bases[column] = p.id

    return DraftConfig(
        feed_id=feed_id, elements=list(elements.values()), element_bases=element_bases,
        mappings=list(mappings.values()), mapping_bases=mapping_bases, transformations=list(transformations.values()),
        transformation_bases=transformation_bases, types={k: e.data_type for k, e in elements.items()},
        blocked=blocked, known_mapping_proposals=frozenset(known | set(mapping_bases.values())))


def check_unchanged(session: Session, draft: DraftConfig) -> None:
    """before_write, under the feed lock: every draft the input was built on is still pending or now approved (an
    approval during the call changes nothing the agent saw), and no mapping was approved that the input did not know.
    ConfigurationChangedMeanwhile otherwise (a discard row, nothing written)."""
    proposals = ProposalRepository(session)
    for proposal_id in sorted(draft.used):
        status = proposals.get(proposal_id).status
        if status not in ("pending", "approved"):
            raise ConfigurationChangedMeanwhile(f"Draft {proposal_id} the agent was given is now {status}")
    unknown = {m.proposal_id for m in MappingRepository(session).current_for_feed(draft.feed_id)} \
        - draft.known_mapping_proposals
    if unknown:
        raise ConfigurationChangedMeanwhile(f"A mapping of feed {draft.feed_id} was approved while the agent answered")
