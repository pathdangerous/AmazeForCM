"""Known source? (core/steps/known_source.py): from a requirement with status defined.

Outcomes (CLAUDE.md): known_source -> 02; no_known_source -> S1. Phase 3 plan, assumptions 6 to 11:
- known_source when the requirement has elements, no ticked terms, and one known source declares every
  element; with several, the fewest declared elements beyond the request, then the code;
- any ticked term gives no_known_source;
- known_source selects the source: a candidate feed (one per source), a source_choices row, and the
  requirement moves to source_selected. no_known_source writes nothing.
"""

import json

import pytest
from sqlalchemy import func, select, text

from core.repositories.errors import RequirementNotInStatus
from core.repositories.feeds import FeedRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.source_choices import SourceChoiceRepository
from core.repositories.tables import FeedRow, SourceChoiceRow
from core.steps.known_source import KnownSource, NoKnownSource, check_known_source, find_known_source
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue

CATALOGUE = SourceCatalogue.load(DEFAULT_CATALOGUE)


@pytest.fixture
def requirement(db_session, glossary):
    def _make(element_keys=(), terms=(), title="request"):
        return RequirementRepository(db_session).create(
            requester_id="dev-requester", title=title, element_keys=list(element_keys), terms=list(terms)
        )

    return _make


def entry(code, elements, *, known=True):
    return {"code": code, "name": code, "provider": "Synthetic", "description": "Synthetic.",
            "visibility": "public", "channel": "exchanges", "known": known,
            "conditions": {"licence": "L", "permitted_use": "U", "redistribution": "R", "external_approvals": []},
            "elements": elements, "other_fields": [], "connector": "local_file", "source_ref": f"files/{code}.csv",
            "columns": {f"col_{e}": e for e in elements}}


def catalogue_of(tmp_path, *entries):
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps({"sources": list(entries)}), encoding="utf-8")
    return SourceCatalogue.load(path)


def count(db_session, table):
    return db_session.scalar(select(func.count()).select_from(table))


# ---- the rule (pure)


def test_known_source_when_one_known_source_declares_every_element(requirement):
    result = check_known_source(requirement(["volume"]), CATALOGUE)
    assert isinstance(result, KnownSource)
    assert (result.source.code, result.source.name) == ("syn_exchange_volumes", "Exchange daily volumes")
    assert result.source.conditions.licence == "Synthetic exchange data licence"
    # a subset of what the source declares is enough
    assert check_known_source(requirement(["instrument_id", "volume"]), CATALOGUE).source.code == "syn_exchange_volumes"


def test_no_known_source_when_none_covers_or_a_term_was_ticked(requirement):
    """open_price is declared only by a source that is not known; a ticked term is free text."""
    none_covers = check_known_source(requirement(["open_price"]), CATALOGUE)
    assert isinstance(none_covers, NoKnownSource) and none_covers.reason == "no_known_source_covers"
    term = check_known_source(requirement(["volume"], ["esg score"]), CATALOGUE)
    assert isinstance(term, NoKnownSource) and term.reason == "terms_ticked"
    only_terms = check_known_source(requirement(terms=["esg score"]), CATALOGUE)
    assert only_terms.reason == "terms_ticked"


def test_one_source_must_declare_every_element(requirement):
    """volume and accrued_interest are each known, but no single known source declares both."""
    result = check_known_source(requirement(["volume", "accrued_interest"]), CATALOGUE)
    assert isinstance(result, NoKnownSource) and result.reason == "no_known_source_covers"


def test_the_fewest_extra_elements_then_the_code(tmp_path, requirement):
    catalogue = catalogue_of(
        tmp_path,
        entry("syn_wide", ["volume", "close_price", "open_price"]),
        entry("syn_b_lean", ["volume", "close_price"]),
        entry("syn_a_lean", ["volume", "price_date"]),
        entry("syn_not_known", ["volume"], known=False),
    )
    assert check_known_source(requirement(["volume"]), catalogue).source.code == "syn_a_lean"


# ---- the step on the database


def test_find_selects_the_source_and_hands_over_a_candidate_feed(db_session, requirement):
    req = requirement(["volume"])
    result = find_known_source(db_session, requirement_id=req.id, catalogue=CATALOGUE)
    assert isinstance(result.check, KnownSource)
    feed = FeedRepository(db_session).get_by_source("syn_exchange_volumes")
    assert (feed.code, feed.stage, feed.connector, feed.source_ref, feed.requirement_id) == (
        "syn_exchange_volumes", "candidate", "local_file", "files/exchange_volumes.csv", req.id,
    )
    assert result.feed == feed
    choice = SourceChoiceRepository(db_session).current(req.id)
    assert (choice.feed_id, choice.basis, choice.version, choice.action) == (feed.id, "known_source", 1, "selected")
    assert RequirementRepository(db_session).get(req.id).status == "source_selected"


def test_a_second_requirement_reuses_the_feed(db_session, requirement):
    first, second = requirement(["volume"], title="first"), requirement(["volume"], title="second")
    find_known_source(db_session, requirement_id=first.id, catalogue=CATALOGUE)
    find_known_source(db_session, requirement_id=second.id, catalogue=CATALOGUE)
    assert count(db_session, FeedRow) == 1
    feed = FeedRepository(db_session).get_by_source("syn_exchange_volumes")
    assert SourceChoiceRepository(db_session).requirements_selecting(feed.id) == [first.id, second.id]


def test_no_known_source_writes_nothing(db_session, requirement):
    req = requirement(["open_price"])
    result = find_known_source(db_session, requirement_id=req.id, catalogue=CATALOGUE)
    assert isinstance(result.check, NoKnownSource) and result.feed is None
    assert (count(db_session, FeedRow), count(db_session, SourceChoiceRow)) == (0, 0)
    assert RequirementRepository(db_session).get(req.id).status == "defined"


@pytest.mark.parametrize("elements", [["volume"], ["open_price"]])  # would be known_source / no_known_source
def test_find_needs_status_defined(db_session, requirement, elements):
    """A requirement that has moved on is refused, whatever the outcome would be, and nothing is written."""
    req = requirement(elements)
    db_session.execute(text("UPDATE requirements SET status = 'onboarding' WHERE id = :id"), {"id": req.id})
    db_session.expire_all()
    with pytest.raises(RequirementNotInStatus, match="onboarding"):
        find_known_source(db_session, requirement_id=req.id, catalogue=CATALOGUE)
    assert (count(db_session, FeedRow), count(db_session, SourceChoiceRow)) == (0, 0)


def test_a_rejected_source_is_not_a_known_source(db_session, requirement):
    """Phase 4 plan, assumption 25: the backend team rejected syn_exchange_volumes's candidate feed."""
    from tests.onboarded import rejected_feed

    first = requirement(["volume"])
    feed = find_known_source(db_session, requirement_id=first.id, catalogue=CATALOGUE).feed
    rejected_feed(db_session, feed.id)
    later = requirement(["volume"])
    result = find_known_source(db_session, requirement_id=later.id, catalogue=CATALOGUE)
    assert isinstance(result.check, NoKnownSource) and result.feed is None
    assert result.check.reason == "no_known_source_covers"


def test_a_source_rejected_between_the_check_and_the_lock_is_409(db_session, requirement, monkeypatch):
    """The check is made unlocked, then the source code is locked. A rejection that commits in between is
    found once the lock is held: SourceRejected (409), and nothing is written for the requirement."""
    import core.steps.known_source as known_source
    from core.repositories.errors import SourceRejected
    from core.repositories.source_choices import SourceChoiceRepository
    from tests.onboarded import rejected_feed

    feed = find_known_source(db_session, requirement_id=requirement(["volume"]).id, catalogue=CATALOGUE).feed
    later = requirement(["volume"])
    original, done = known_source.lock_source, []

    def rejected_meanwhile(session, code):  # the backend team's rejection commits just before this lock
        if not done:  # once: feed_for takes the same lock again later
            rejected_feed(session, feed.id)
            done.append(code)
        original(session, code)

    monkeypatch.setattr(known_source, "lock_source", rejected_meanwhile)
    with pytest.raises(SourceRejected, match="syn_exchange_volumes"):
        find_known_source(db_session, requirement_id=later.id, catalogue=CATALOGUE)
    assert SourceChoiceRepository(db_session).current(later.id) is None
    assert RequirementRepository(db_session).get(later.id).status == "defined"


def test_a_second_click_is_refused(db_session, requirement):
    req = requirement(["volume"])
    find_known_source(db_session, requirement_id=req.id, catalogue=CATALOGUE)
    with pytest.raises(RequirementNotInStatus, match="source_selected"):
        find_known_source(db_session, requirement_id=req.id, catalogue=CATALOGUE)
    assert (count(db_session, FeedRow), count(db_session, SourceChoiceRow)) == (1, 1)
