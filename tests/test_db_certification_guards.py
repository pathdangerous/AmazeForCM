"""Migration 0019's certification, product and glossary run guards (phase 7 plan, section 6: G14 to G16), by raw SQL.

G14: load_certifications (amaze_guard_load_certification, its checks and unique load_id; G4 for the table is in
test_append_only.py). G15: products.feed_id (uq_products_feed_id, amaze_guard_product_feed). G16:
ck_glossary_runs_reason gains structure_change. The helpers (live, scheduled_loaded, certified, CERT) are in
test_db_arrival_guards.py. Each refusal goes through answered_by(), as there. Against 0018 every test fails.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from core.repositories.feeds import FeedRepository
from tests.configured import a_registered_feed
from tests.test_db_arrival_guards import (  # noqa: F401 (live and s are fixtures)
    CERT, cert_params, certified, live, s, scheduled, scheduled_loaded)
from tests.test_db_load_guards import MOVE, STAGES, add_stage, new_load
from tests.test_db_onboarding_guards import _branch_off, answered_by


def cert_guard(message):
    return _branch_off("amaze_guard_load_certification", message)


def product_guard(message):
    return _branch_off("amaze_guard_product_feed", message)


# ---------------------------------------------------------------- G14: a certification recounts the load


def test_a_loaded_scheduled_load_is_certified(s, live):
    load = scheduled_loaded(s, live)
    s.execute(text(CERT), cert_params(load, live[0]))
    assert s.scalar(text("SELECT outcome FROM load_certifications WHERE load_id = :l"), {"l": load}) == "released"


def a_received_scheduled_load(s, live):
    return scheduled(s, live)


def a_go_live_test_load(s, live):
    load = new_load(s, live[0])
    for stage in STAGES:
        add_stage(s, load, stage)
    s.execute(text(MOVE), {"status": "loaded", "id": load})
    return load


@pytest.mark.parametrize("make", [a_received_scheduled_load, a_go_live_test_load], ids=["received", "go_live_test"])
def test_only_a_loaded_scheduled_load_is_certified(s, live, make):
    answered_by(s, "is not a loaded scheduled load of feed", CERT, cert_params(make(s, live), live[0]),
                cert_guard("load_certifications: load % is not a loaded scheduled load of feed %"))


def test_a_load_is_certified_for_its_own_feed(s, live):
    other = a_registered_feed(s, "syn_certification_other")
    answered_by(s, "is not a loaded scheduled load of feed", CERT, cert_params(scheduled_loaded(s, live), other),
                cert_guard("load_certifications: load % is not a loaded scheduled load of feed %"))


@pytest.mark.parametrize("over", [{"rows": 9}, {"flagged": 1}])
def test_a_certification_counts_the_standard_records(s, live, over):
    load = scheduled_loaded(s, live)
    answered_by(s, "standard records and", CERT, cert_params(load, live[0], **over),
                cert_guard("load_certifications: load % has % standard records and % flagged, not % and %"))


def test_the_trust_score_is_the_formula(s, live):
    load = scheduled_loaded(s, live)  # 10 rows, none flagged: 100.0
    answered_by(s, "is not 100 x unflagged / rows", CERT, cert_params(load, live[0], score=99.0),
                cert_guard("load_certifications: trust score % of load % is not 100 x unflagged / rows (%)"))


@pytest.mark.parametrize(("flagged", "outcome"), [(0, "held_consumers_warned"), (5, "released"), (1, "held_consumers_warned")])
def test_the_outcome_fits_score_and_threshold(s, live, flagged, outcome):
    """5 of 10 flagged scores 50.0, below 90: held. 1 of 10 scores 90.0, at the threshold: released."""
    load = scheduled_loaded(s, live, flagged=flagged)
    answered_by(s, "does not fit score", CERT, cert_params(load, live[0], flagged=flagged, outcome=outcome),
                cert_guard("load_certifications: outcome % does not fit score % and threshold %"))


def test_a_score_at_the_threshold_is_released(s, live):
    load = scheduled_loaded(s, live, flagged=1)
    s.execute(text(CERT), cert_params(load, live[0], flagged=1, threshold=90.0))
    assert s.scalar(text("SELECT outcome FROM load_certifications WHERE load_id = :l"), {"l": load}) == "released"


def test_a_load_is_certified_once(s, live):
    load = scheduled_loaded(s, live)
    s.execute(text(CERT), cert_params(load, live[0]))
    answered_by(s, "uq_load_certifications_load_id", CERT, cert_params(load, live[0]),
                "ALTER TABLE load_certifications DROP CONSTRAINT uq_load_certifications_load_id")


@pytest.mark.parametrize(("name", "over"), [
    ("ck_load_certifications_outcome", {"outcome": "bogus"}),
    ("ck_load_certifications_counts", {"flagged": 11}),
    ("ck_load_certifications_score", {"score": 101.0}),
    ("ck_load_certifications_score", {"threshold": 0.0})])
def test_a_certification_has_known_values(s, live, name, over):
    """The guard trigger answers first, so it is switched off here to reach the checks."""
    s.execute(text("ALTER TABLE load_certifications DISABLE TRIGGER trg_load_certifications_b_guard"))
    load = scheduled_loaded(s, live)
    answered_by(s, name, CERT, cert_params(load, live[0], **over), f"ALTER TABLE load_certifications DROP CONSTRAINT {name}")


# ---------------------------------------------------------------- G15: a product of a feed


PRODUCT = ("INSERT INTO products (code, name, domain, owner_id, visibility, feed_id) "
           "VALUES (:code, 'Synthetic product', 'pricing', :owner, :visibility, :feed)")


def product_params(s, feed, code="syn_product", **over):
    owner, visibility = s.execute(text("SELECT owner_id, visibility FROM feed_registrations WHERE feed_id = :f "
                                       "ORDER BY version DESC LIMIT 1"), {"f": feed}).one()
    return {"code": code, "owner": owner, "visibility": visibility, "feed": feed, **over}


def release(s, live, **kw):
    load = certified(s, live, **kw)
    s.execute(text(MOVE), {"status": "released" if not kw.get("flagged") else "held", "id": load})
    return load


def test_a_product_of_a_released_feed_takes_the_registrations_owner_and_visibility(s, live):
    release(s, live)
    s.execute(text(PRODUCT), product_params(s, live[0]))
    assert s.scalar(text("SELECT feed_id FROM products WHERE code = 'syn_product'")) == live[0]


def test_a_feed_has_one_product(s, live):
    release(s, live)
    s.execute(text(PRODUCT), product_params(s, live[0]))
    answered_by(s, "uq_products_feed_id", PRODUCT, product_params(s, live[0], code="syn_product_two"),
                "DROP INDEX uq_products_feed_id")


def test_products_with_no_feed_are_not_limited_to_one(s):
    for code in ("syn_no_feed_one", "syn_no_feed_two"):
        s.execute(text(PRODUCT), {"code": code, "owner": "dev-data-owner", "visibility": "open", "feed": None})


def test_a_product_of_a_candidate_feed_is_refused(s):
    feed = FeedRepository(s).create_candidate(code="syn_candidate_product", name="Candidate").id
    answered_by(s, "is not registered", PRODUCT,
                {"code": "syn_product", "owner": "dev-data-owner", "visibility": "open", "feed": feed},
                product_guard("products: feed % is not registered"))


@pytest.mark.parametrize("state", ["no load", "loaded", "held"])
def test_a_product_needs_a_released_load(s, live, state):
    if state == "loaded":
        certified(s, live)
    elif state == "held":
        release(s, live, flagged=5)
    answered_by(s, "has no released load", PRODUCT, product_params(s, live[0]),
                product_guard("products: feed % has no released load"))


@pytest.mark.parametrize("over", [{"owner": "someone-else"}, {"visibility": "restricted"}])
def test_a_product_has_the_latest_registrations_owner_and_visibility(s, live, over):
    release(s, live)
    answered_by(s, "do not match the latest registration", PRODUCT, product_params(s, live[0], **over),
                product_guard("products: owner % and visibility % do not match the latest registration of feed % "
                              "(% and %)"))


def test_a_product_cannot_be_given_an_unreleased_feed_afterwards(s, live):
    s.execute(text(PRODUCT), {"code": "syn_product", "owner": "dev-data-owner", "visibility": "open", "feed": None})
    answered_by(s, "has no released load", "UPDATE products SET feed_id = :feed WHERE code = 'syn_product'",
                {"feed": live[0]}, product_guard("products: feed % has no released load"))


# ---------------------------------------------------------------- G16: the fourth glossary run reason

RUN = ("INSERT INTO glossary_runs (feed_id, profile_id, reason, columns, requested_by, status, attempt) "
       "VALUES (:f, :p, :reason, ARRAY['new_col'], 'step_12', 'queued', 0)")


def run_params(s, live, reason):
    profile = s.scalar(text("SELECT id FROM feed_profiles WHERE feed_id = :f ORDER BY version DESC LIMIT 1"),
                       {"f": live[0]})
    return {"f": live[0], "p": profile, "reason": reason}


def test_a_structure_change_is_the_fourth_glossary_run_reason(s, live):
    """structure_change is accepted; a reason outside the four is refused by the check, and by nothing else."""
    s.execute(text(RUN), run_params(s, live, "structure_change"))
    answered_by(s, "ck_glossary_runs_reason", RUN, run_params(s, live, "bogus"),
                "ALTER TABLE glossary_runs DROP CONSTRAINT ck_glossary_runs_reason")
