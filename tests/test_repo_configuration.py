"""Mapping (level 1: one source field to one glossary element) and Rule (SQL) configuration.

Both are versioned and append-only (rule 7), and a version can only be created from an approved
proposal of the matching kind (rule 1). The database refuses a mapping to an element_key that is not
in the glossary, even when the repository is bypassed.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.errors import (
    FeedNotFound,
    ProposalForAnotherFeed,
    ProposalNotApproved,
    UnknownGlossaryElement,
    WrongProposalKind,
)
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository

STEWARD_THEN_CHECKER = ("data_steward", "checker")


@pytest.fixture
def feed(db_session):
    glossary = GlossaryRepository(db_session)
    for key, data_type in (("close_price", "decimal"), ("isin", "string")):
        glossary.add_seed_element(key=key, name=key.replace("_", " ").title(), domain="pricing",
                                  data_type=data_type, definition="Synthetic definition.", aliases=[])
    return FeedRepository(db_session).create_candidate(code="synthetic_prices", name="Synthetic prices")


@pytest.fixture
def mappings(db_session):
    return MappingRepository(db_session)


@pytest.fixture
def rules(db_session):
    return RuleRepository(db_session)


# ---- mappings


def test_mapping_versions_and_current(mappings, feed, db_session):
    from tests.configured import approved_mapping

    first = approved_mapping(db_session, feed.id, "ClosePx", "isin")
    second = approved_mapping(db_session, feed.id, "ClosePx", "close_price")
    approved_mapping(db_session, feed.id, "Code", "isin")
    assert (first.version, second.version) == (1, 2)
    assert [(m.source_field, m.element_key, m.version) for m in mappings.current_for_feed(feed.id)] == [
        ("ClosePx", "close_price", 2), ("Code", "isin", 1),
    ]
    assert [m.element_key for m in mappings.history(feed.id, "ClosePx")] == ["isin", "close_price"]


def test_mapping_needs_an_approved_mapping_proposal(mappings, feed, db_session, approved_proposal):
    from tests.configured import mapping_proposal

    with pytest.raises(ProposalNotApproved):
        mappings.add_version(feed_id=feed.id, source_field="ClosePx", element_key="close_price",
                             proposal_id=mapping_proposal(db_session, feed.id, "ClosePx", ["close_price"]).id)
    with pytest.raises(WrongProposalKind):
        mappings.add_version(feed_id=feed.id, source_field="ClosePx", element_key="close_price",
                             proposal_id=approved_proposal(kind="rule", roles=STEWARD_THEN_CHECKER).id)
    assert mappings.current_for_feed(feed.id) == []


def test_mapping_to_an_unknown_element(mappings, feed, db_session):
    from tests.configured import mapping_proposal

    proposal = mapping_proposal(db_session, feed.id, "X", ["no_such_element"], decision="approved")
    with pytest.raises(UnknownGlossaryElement, match="no_such_element"):
        mappings.add_version(feed_id=feed.id, source_field="X", element_key="no_such_element", proposal_id=proposal.id)


def test_mapping_for_an_unknown_feed(mappings, feed, db_session):
    from tests.configured import mapping_proposal

    proposal = mapping_proposal(db_session, feed.id, "X", ["isin"], decision="approved")
    with pytest.raises(FeedNotFound):
        mappings.add_version(feed_id=uuid.uuid4(), source_field="X", element_key="isin", proposal_id=proposal.id)


def test_proposal_approved_for_another_feed(mappings, feed, db_session):
    """A mapping proposal with no feed cannot exist from 0017 (ck_proposals_names_a_column)."""
    from tests.configured import a_registered_feed, mapping_proposal

    other = mapping_proposal(db_session, a_registered_feed(db_session, "other_feed"), "X", ["isin"], decision="approved")
    with pytest.raises(ProposalForAnotherFeed):
        mappings.add_version(feed_id=feed.id, source_field="X", element_key="isin", proposal_id=other.id)


def test_a_mapping_needs_the_proposals_column_and_one_of_its_candidates(mappings, feed, db_session):
    from core.repositories.errors import NotAProposalOption, ProposalForAnotherField
    from tests.configured import mapping_proposal

    proposal = mapping_proposal(db_session, feed.id, "X", ["isin", "close_price"], decision="approved")
    with pytest.raises(ProposalForAnotherField):
        mappings.add_version(feed_id=feed.id, source_field="Y", element_key="isin", proposal_id=proposal.id)
    with pytest.raises(NotAProposalOption):
        mappings.add_version(feed_id=feed.id, source_field="X", element_key="volume", proposal_id=proposal.id)
    assert mappings.add_version(feed_id=feed.id, source_field="X", element_key="close_price",
                                proposal_id=proposal.id).version == 1


def test_a_mapping_needs_a_registered_feed(mappings, db_session, approved_proposal):
    from core.repositories.errors import FeedNotRegistered

    candidate = FeedRepository(db_session).create_candidate(code="candidate_feed", name="Candidate feed")
    proposal = approved_proposal(kind="mapping", feed_id=candidate.id,
                                 payload={"source_field": "X", "candidates": ["isin"]})
    with pytest.raises(FeedNotRegistered):
        mappings.add_version(feed_id=candidate.id, source_field="X", element_key="isin", proposal_id=proposal.id)


def test_rejected_elements_are_every_candidate_of_a_rejected_proposal_of_the_column(mappings, feed, db_session):
    from tests.configured import mapping_proposal

    mapping_proposal(db_session, feed.id, "X", ["isin", "close_price"], decision="rejected")
    mapping_proposal(db_session, feed.id, "Y", ["volume"], decision="rejected")
    mapping_proposal(db_session, feed.id, "X", ["volume"], decision="approved")
    assert mappings.rejected_elements(feed.id, "X") == {"isin", "close_price"}
    assert mappings.rejected_elements(feed.id, "Z") == set()


# ---- the glossary key trigger (approved change 2)

RAW_MAPPING = text(
    "INSERT INTO mappings (feed_id, source_field, element_key, version, proposal_id) "
    "VALUES (:feed, 'RawField', :key, 1, :proposal)"
)


def _raw_proposal(db_session, feed, key):
    from tests.configured import mapping_proposal

    return mapping_proposal(db_session, feed.id, "RawField", [key], decision="approved")


def test_database_refuses_a_mapping_to_a_key_not_in_the_glossary(db_session, feed):
    proposal = _raw_proposal(db_session, feed, "not_in_glossary")
    with pytest.raises(IntegrityError, match="element_key not_in_glossary is not in the glossary"):
        db_session.execute(RAW_MAPPING, {"feed": feed.id, "key": "not_in_glossary", "proposal": proposal.id})


def test_database_accepts_a_mapping_to_a_glossary_key(db_session, feed):
    proposal = _raw_proposal(db_session, feed, "close_price")
    db_session.execute(RAW_MAPPING, {"feed": feed.id, "key": "close_price", "proposal": proposal.id})
    assert db_session.execute(text("SELECT count(*) FROM mappings")).scalar_one() == 1


def test_database_refuses_a_mapping_without_a_proposal(db_session, feed):
    with pytest.raises(IntegrityError, match="proposal_id"):
        db_session.execute(RAW_MAPPING, {"feed": feed.id, "key": "close_price", "proposal": None})


# ---- rules


def test_rule_versions_and_current(rules, feed, approved_proposal):
    first = rules.add_version(feed_id=feed.id, rule_key="close_price_positive",
                              sql="SELECT * FROM {table} WHERE close_price <= 0",
                              description="Close price must be positive.",
                              proposal_id=approved_proposal(kind="rule", roles=STEWARD_THEN_CHECKER).id)
    second = rules.add_version(feed_id=feed.id, rule_key="close_price_positive",
                               sql="SELECT * FROM {table} WHERE close_price IS NULL OR close_price <= 0",
                               description="Close price must be present and positive.",
                               proposal_id=approved_proposal(kind="rule", roles=STEWARD_THEN_CHECKER).id)
    assert (first.version, second.version) == (1, 2)
    (current,) = rules.current_for_feed(feed.id)
    assert (current.rule_key, current.version) == ("close_price_positive", 2)
    assert [r.version for r in rules.history(feed.id, "close_price_positive")] == [1, 2]


def test_rule_needs_an_approved_rule_proposal(rules, feed, make_proposal, approved_proposal):
    fields = {"feed_id": feed.id, "rule_key": "r", "sql": "SELECT 1", "description": "Synthetic."}
    with pytest.raises(ProposalNotApproved):
        rules.add_version(**fields, proposal_id=make_proposal(kind="rule", roles=STEWARD_THEN_CHECKER).id)
    with pytest.raises(WrongProposalKind):
        rules.add_version(**fields, proposal_id=approved_proposal(kind="mapping").id)
    with pytest.raises(FeedNotFound):
        rules.add_version(**{**fields, "feed_id": uuid.uuid4()},
                          proposal_id=approved_proposal(kind="rule", roles=STEWARD_THEN_CHECKER).id)
    assert rules.current_for_feed(feed.id) == []
