"""A column added after the first release joins the product (phase 7.19, T5; plan docs/plans/phase-7.19.md, assumptions 13
to 15). Called by step 20 for a released load of a feed that already has its product. Rules only: no agent, no model call.

The load being finished ran on a go-live whose mappings are the feed's current ones (the engine refuses any other), so every
element they name that the product does not hold is added to it. Nothing is ever removed: product_elements is append-only,
and an element the mappings no longer name is only left out of what consumers see (core/steps/product_page.py). Each
consumer is told once per new column: the requesters of the requirements that selected the feed, and the requester the
product was made for. A held or failed load never reaches step 20, so nothing is added before a release.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.domain.product import Product
from core.repositories.configuration import MappingRepository
from core.repositories.glossary import GlossaryRepository
from core.repositories.products import ProductRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from ports.notify.base import Notifier


def gain_elements(session: Session, product: Product, notifier: Notifier) -> list[str]:
    """The element keys added to the product by this release (none: nothing written, nobody told)."""
    products = ProductRepository(session)
    mapped = {m.element_key for m in MappingRepository(session).current_for_feed(product.feed_id)}
    gained = sorted(mapped - set(products.elements_of(product.id)))
    if not gained:
        return []
    products.add_elements(product.id, gained)
    requirements = RequirementRepository(session)
    selecting = SourceChoiceRepository(session).requirements_selecting(product.feed_id)
    made_for = [product.requirement_id] if product.requirement_id else []
    consumers = list(dict.fromkeys(requirements.get(i).requester_id for i in [*selecting, *made_for]))
    glossary = GlossaryRepository(session)
    for key in gained:
        name = glossary.get_current(key).name
        for person in consumers:
            notifier.notify(recipient=person, subject=f"{product.name} gained column {name}",
                            body=f"{product.name} now has the column {name} ({key}). Releases from before it show it empty.",
                            link=f"#/product/{product.code}")
    return gained
