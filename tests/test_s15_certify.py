"""Step 15, certify (phase 7 plan, assumptions 19 to 23; sections 5.1 to 5.3).

Outcomes (CLAUDE.md): released, held_consumers_warned. Deterministic: the trust score is 100 x unflagged rows / rows,
from standard_records.flags and the validated stage's counts (phase 6 note 2); released at or above the threshold.
Step 15 runs in the same transaction as the engine's write (finish_scheduled_load), so a refusal fails the load.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from core.repositories.certifications import LoadCertificationRepository
from core.repositories.loads import LoadRepository
from core.steps.s15_certify import HeldConsumersWarned, Released
from core.steps.s20_create_product import FinishContext
from ports.notify.inapp import InAppNotifier, NotificationRow
from ports.storage.local import LocalBlobStore
from tests.configured import VOLUME_RULE, loadable_feed, onboarding_requirement, volume_csv
from tests.step_chains import run_scheduled

OPERATIONS, TEAM = ["dev-operations"], ["dev-backend-team"]


@pytest.fixture
def world(db_session, glossary, tmp_path):
    feed, live = loadable_feed(db_session)
    blobs = LocalBlobStore(tmp_path / "blob")
    context = FinishContext(notifier=InAppNotifier(db_session), threshold=Decimal("90"), operations=OPERATIONS,
                            backend_team=TEAM)
    return SimpleNamespace(feed=feed, live=live, session=db_session, blobs=blobs, context=context, n=0)


def deliver(world, content: str, **kw):
    world.n += 1
    key = f"raw/syn_loadable/{world.n}/f.csv"
    world.blobs.put(key, content.encode())
    return run_scheduled(world.session, world.blobs, world.feed, world.live.id, file_ref=key,
                         context=kw.pop("context", world.context), **kw)


def told(world, subject_start: str) -> set[str]:
    rows = world.session.scalars(select(NotificationRow).where(NotificationRow.subject.like(f"{subject_start}%")))
    return {r.recipient for r in rows}


def test_a_load_at_or_above_the_threshold_is_released(world):
    finished = deliver(world, volume_csv(10, 1))  # 1 of 10 rows flagged: 90.0, the threshold itself
    certification = finished.certification
    assert isinstance(certification, Released) and certification.outcome == "released"
    assert (certification.trust_score, certification.threshold, certification.rows, certification.flagged_rows) == (
        Decimal("90.0"), Decimal("90.0"), 10, 1)
    assert finished.run.load.status == "released"
    stored = LoadCertificationRepository(world.session).for_load(finished.run.load.id)
    assert (stored.rows, stored.flagged_rows, stored.rule_counts, stored.outcome, stored.warned) == (
        10, 1, {VOLUME_RULE: 1}, "released", [])


def test_a_load_below_the_threshold_is_held_and_its_consumers_warned(world):
    onboarding_requirement(world.session, world.feed, elements=["volume"])
    finished = deliver(world, volume_csv(10, 2))  # 80.0
    certification = finished.certification
    assert isinstance(certification, HeldConsumersWarned) and certification.outcome == "held_consumers_warned"
    assert certification.trust_score == Decimal("80.0") and finished.run.load.status == "held"
    # the consumers, operations, the backend team (no product yet) and, from 7.14, the feed's registered owner
    people = {"dev-requester", *OPERATIONS, *TEAM, "dev-data-owner"}
    assert set(certification.warned) == people and told(world, "Load held") == people
    links = world.session.scalars(select(NotificationRow.link).where(NotificationRow.subject.like("Load held%")))
    assert set(links) == {f"#/feed/{finished.run.load.feed_id}"}  # phase 7.5, P28 (plan, assumption 16)
    assert LoadCertificationRepository(world.session).for_load(finished.run.load.id).warned == certification.warned
    assert finished.product_code is None  # nothing was released, so no product (step 20 runs on release only)


def test_a_feed_with_no_flag_scores_a_hundred(world):
    finished = deliver(world, volume_csv(4, 0))
    assert finished.certification.trust_score == Decimal("100.0") and finished.run.load.status == "released"


def test_a_failed_load_is_not_certified(world):
    finished = deliver(world, "instr_id,trade_date,traded_qty\nsyn-1,not-a-date,5\n")
    assert finished.run.load.status == "failed_rolled_back" and finished.certification is None
    assert LoadCertificationRepository(world.session).for_load(finished.run.load.id) is None


def test_a_held_load_stays_held_and_the_next_delivery_is_certified_on_its_own(world):
    held = deliver(world, volume_csv(10, 5))
    better = deliver(world, volume_csv(10, 0))
    assert held.run.load.status == "held" and better.run.load.status == "released"
    assert LoadRepository(world.session).get(held.run.load.id).status == "held"


def test_a_certification_the_database_refuses_fails_the_load_naming_certify(world, monkeypatch):
    real = LoadCertificationRepository.add

    def wrong_score(self, **fields):
        return real(self, **{**fields, "trust_score": Decimal("99.9")})

    monkeypatch.setattr(LoadCertificationRepository, "add", wrong_score)
    finished = deliver(world, volume_csv(10, 1))
    load = finished.run.load
    assert load.status == "failed_rolled_back" and load.failed_stage == "standard"
    assert load.failure.startswith("certify: ") and "not 100 x unflagged / rows" in load.failure
    assert finished.certification is None
    assert LoadCertificationRepository(world.session).for_load(load.id) is None
