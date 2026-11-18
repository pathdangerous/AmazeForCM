"""Step 20, create product (phase 7 plan, assumptions 24 to 29; sections 5.1 to 5.3).

Outcomes (CLAUDE.md): created, data_not_in_domains. On a release (step 15 released the load) the feed's product is
created once, with the registration's owner and visibility (phase 4 note 5); each onboarding requirement selecting
the feed becomes ready or access_restricted (created), or is told what is missing and stays onboarding
(data_not_in_domains). Runs in the finishing transaction with the engine's write and step 15.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import select

from core.repositories.feeds import FeedRepository
from core.repositories.loads import LoadRepository
from core.repositories.products import ProductRepository
from core.repositories.requirements import RequirementRepository
from core.steps.s20_create_product import Created, DataNotInDomains, FinishContext, Step20Result
from ports.notify.inapp import InAppNotifier, NotificationRow
from ports.storage.local import LocalBlobStore
from tests.configured import loadable_feed, onboarding_requirement, volume_csv
from tests.step_chains import run_scheduled

OPERATIONS, TEAM = ["dev-operations"], ["dev-backend-team"]
NAMES = {"dev-data-owner": "Devon Owner"}


def world_of(db_session, tmp_path, **feed_kw):
    feed, live = loadable_feed(db_session, **feed_kw)
    context = FinishContext(notifier=InAppNotifier(db_session), threshold=Decimal("90"), operations=OPERATIONS,
                            backend_team=TEAM, roles_of=lambda uid: ["requester"],
                            display_name=lambda uid: NAMES.get(uid, uid))
    return SimpleNamespace(feed=feed, live=live, session=db_session, blobs=LocalBlobStore(tmp_path / "blob"),
                           context=context, n=0)


@pytest.fixture
def world(db_session, glossary, tmp_path):
    return world_of(db_session, tmp_path)


def deliver(world, content: str | None = None):
    world.n += 1
    key = f"raw/syn_loadable/{world.n}/f.csv"
    world.blobs.put(key, (content or volume_csv(10, 0)).encode())
    return run_scheduled(world.session, world.blobs, world.feed, world.live.id, file_ref=key, context=world.context)


def status_of(world, requirement):
    return RequirementRepository(world.session).get(requirement.id).status


def messages(world, recipient):
    rows = world.session.scalars(select(NotificationRow).where(NotificationRow.recipient == recipient))
    return [(r.subject, r.body) for r in rows]


def test_the_first_release_creates_the_product_and_makes_the_requester_ready(world):
    waiting = onboarding_requirement(world.session, world.feed, title="Daily volume", elements=["volume"])
    finished = deliver(world)
    assert finished.run.load.status == "released"
    (made,) = finished.requirements
    assert isinstance(made, Created) and made.outcome == "created"
    assert (made.requirement_id, made.product_code, made.product_new, made.status) == (
        waiting.id, "syn_loadable", True, "ready")
    product = ProductRepository(world.session).for_feed(world.feed)
    feed = FeedRepository(world.session).get(world.feed)
    assert (product.code, product.name, product.feed_id, product.owner_id, product.visibility) == (
        feed.code, feed.name, world.feed, "dev-data-owner", "open")
    assert product.description == f"Released from the {feed.name} feed."
    assert product.requirement_id == waiting.id and product.domain == "pricing"
    assert ProductRepository(world.session).elements_of(product.id) == ["instrument_id", "price_date", "volume"]
    assert status_of(world, waiting) == "ready"
    assert [s for s, _ in messages(world, "dev-requester")] == ["Ready: Daily volume"]
    ready = world.session.scalar(select(NotificationRow.link).where(NotificationRow.subject == "Ready: Daily volume"))
    assert ready == f"#/requirement/{waiting.id}"  # phase 7.5, P28 (plan, assumption 16)


def test_a_restricted_registration_gives_access_restricted_with_the_route(world, db_session, glossary, tmp_path):
    other = world_of(db_session, tmp_path, code="syn_restricted", visibility="restricted")
    waiting = onboarding_requirement(db_session, other.feed, title="Restricted volume", elements=["volume"])
    finished = deliver(other)
    (made,) = finished.requirements
    assert made.status == "access_restricted" and status_of(other, waiting) == "access_restricted"
    assert ProductRepository(db_session).for_feed(other.feed).visibility == "restricted"
    ((subject, body),) = messages(other, "dev-requester")
    assert subject == "Access restricted: Restricted volume" and "Ask Devon Owner for access to" in body


def test_a_requirement_the_product_does_not_cover_stays_onboarding(world):
    waiting = onboarding_requirement(world.session, world.feed, title="Needs more", elements=["volume", "close_price"])
    finished = deliver(world)
    (not_covered,) = finished.requirements
    assert isinstance(not_covered, DataNotInDomains) and not_covered.outcome == "data_not_in_domains"
    assert (not_covered.requirement_id, not_covered.missing, not_covered.terms) == (waiting.id, ["close_price"], [])
    assert status_of(world, waiting) == "onboarding"
    assert messages(world, "dev-requester") == []
    assert [s for s, _ in messages(world, "dev-backend-team")] == ["Requirement not covered: Needs more"]
    told = world.session.scalar(select(NotificationRow.link).where(NotificationRow.recipient == "dev-backend-team"))
    assert told == f"#/feed/{world.feed}"  # phase 7.5, P28 (plan, assumption 16)


def test_an_outstanding_ticked_term_is_data_not_in_domains(world):
    covered = onboarding_requirement(world.session, world.feed, title="Covered term", elements=["volume"],
                                     terms=["traded volume"])  # an alias of volume: the product holds it
    outstanding = onboarding_requirement(world.session, world.feed, title="Open term", elements=["volume"],
                                         terms=["turnover"])
    finished = deliver(world)
    by_id = {r.requirement_id: r for r in finished.requirements}
    assert isinstance(by_id[covered.id], Created)
    assert isinstance(by_id[outstanding.id], DataNotInDomains)
    assert (by_id[outstanding.id].missing, by_id[outstanding.id].terms) == ([], ["turnover"])
    assert status_of(world, outstanding) == "onboarding"
    ((_, body),) = messages(world, "dev-backend-team")
    assert "turnover" in body


def test_a_later_release_reuses_the_product_and_readies_a_requirement_linked_since(world):
    deliver(world)
    product = ProductRepository(world.session).for_feed(world.feed)
    late = onboarding_requirement(world.session, world.feed, title="Linked later", elements=["volume"])
    assert status_of(world, late) == "onboarding"  # assumption 29: not at once
    finished = deliver(world, volume_csv(9, 0))
    (made,) = finished.requirements
    assert made.product_new is False and made.product_code == "syn_loadable" and made.status == "ready"
    assert ProductRepository(world.session).for_feed(world.feed) == product


def test_a_held_load_creates_no_product_and_leaves_requirements_waiting(world):
    waiting = onboarding_requirement(world.session, world.feed, elements=["volume"])
    finished = deliver(world, volume_csv(10, 5))
    assert finished.run.load.status == "held" and finished.requirements == []
    assert ProductRepository(world.session).for_feed(world.feed) is None
    assert status_of(world, waiting) == "onboarding"


def test_a_refused_product_fails_the_load_naming_create_product(world, monkeypatch):
    """G15 refuses a product whose owner or visibility is not the registration's; the whole finish is rolled back."""
    real = ProductRepository.create
    monkeypatch.setattr(ProductRepository, "create", lambda self, **f: real(self, **{**f, "visibility": "restricted"}))
    waiting = onboarding_requirement(world.session, world.feed, elements=["volume"])
    finished = deliver(world)
    load = finished.run.load
    assert load.status == "failed_rolled_back" and load.failed_stage == "standard"
    assert load.failure.startswith("create_product: ") and "do not match the latest registration" in load.failure
    assert finished.certification is None and finished.requirements == []
    assert ProductRepository(world.session).for_feed(world.feed) is None
    assert status_of(world, waiting) == "onboarding"
    assert LoadRepository(world.session).stages(load.id)[-1].outcome == "failed_rolled_back"


def test_the_locks_are_taken_feed_then_requirements_in_id_order_then_load(world, monkeypatch):
    from core.repositories.feeds import FeedRepository as Feeds

    order = []
    for repo, label in ((Feeds, "feed"), (RequirementRepository, "requirement"), (LoadRepository, "load")):
        real = repo.lock

        def spy(self, ident, *a, _real=real, _label=label, **k):
            order.append((_label, ident))
            return _real(self, ident, *a, **k)

        monkeypatch.setattr(repo, "lock", spy)
    first = onboarding_requirement(world.session, world.feed, title="One", elements=["volume"])
    second = onboarding_requirement(world.session, world.feed, title="Two", elements=["volume"])
    deliver(world)
    labels = [label for label, _ in order]
    assert labels[0] == "feed" and labels.index("load") > max(i for i, l in enumerate(labels) if l == "requirement")
    requirement_ids = [i for label, i in order if label == "requirement"][:2]
    assert requirement_ids == sorted([first.id, second.id])


def test_the_results_are_typed_and_round_trip():
    adapter = TypeAdapter(Step20Result)
    for value in (Created(requirement_id=uuid4(), product_code="syn_x", product_new=True, status="ready"),
                  DataNotInDomains(requirement_id=uuid4(), missing=["close_price"], terms=["turnover"])):
        assert adapter.validate_python(adapter.dump_python(value)) == value
