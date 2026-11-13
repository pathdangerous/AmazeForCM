"""GlossaryRepository: versioned glossary elements (append-only rows; the current one is the highest version).

A new version can only come from an approved glossary_element proposal (rule 1); only the seed may
insert without one, and only version 1 of a key that does not exist yet.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.repositories.errors import ProposalNotApproved, UnknownGlossaryElement, WrongProposalKind
from core.repositories.glossary import GlossaryRepository

ISIN = {
    "key": "isin", "name": "ISIN", "domain": "security_master", "data_type": "string",
    "definition": "International Securities Identification Number of the instrument (ISO 6166).",
    "aliases": ["isin_code"],
}
CLOSE = {
    "key": "close_price", "name": "Close price", "domain": "pricing", "data_type": "decimal",
    "definition": "Last traded price of the instrument at the end of the trading day.",
    "aliases": ["closing_price", "close"],
}


@pytest.fixture
def repo(db_session):
    return GlossaryRepository(db_session)


@pytest.fixture
def seeded(repo):
    repo.add_seed_element(**ISIN)
    repo.add_seed_element(**CLOSE)
    return repo


def test_seed_inserts_version_1_once(repo):
    assert repo.add_seed_element(**ISIN) is True
    assert repo.add_seed_element(**{**ISIN, "name": "Changed"}) is False
    element = repo.get_current("isin")
    assert (element.version, element.name, element.created_by, element.proposal_id) == (1, "ISIN", "seed", None)


def test_list_current_filters_and_search(seeded):
    assert [e.key for e in seeded.list_current()] == ["close_price", "isin"]
    assert [e.key for e in seeded.list_current(domain="pricing")] == ["close_price"]
    for q in ("ISIN", "isin_code", "closing", "end of the trading day"):  # key, alias, alias, definition
        assert len(seeded.list_current(q=q)) == 1, q
    assert seeded.list_current(q="no such thing") == []
    assert seeded.list_current(domain="pricing", q="isin") == []


def test_domains_with_counts(seeded):
    assert seeded.domains() == [("pricing", 1), ("security_master", 1)]


def test_unknown_element(repo):
    with pytest.raises(UnknownGlossaryElement, match="no_such_key"):
        repo.get_current("no_such_key")
    with pytest.raises(UnknownGlossaryElement):
        repo.history("no_such_key")
    assert repo.exists("no_such_key") is False


def test_new_version_needs_an_approved_glossary_proposal(seeded, make_proposal, approved_proposal):
    changed = {**ISIN, "definition": "ISO 6166 identifier of the instrument. Synthetic test wording."}
    pending = make_proposal(kind="glossary_element", roles=("glossary_owner",))
    with pytest.raises(ProposalNotApproved):
        seeded.add_version(**changed, proposal_id=pending.id, created_by="dev-glossary-owner")
    wrong_kind = approved_proposal(kind="mapping", roles=("data_steward",))
    with pytest.raises(WrongProposalKind):
        seeded.add_version(**changed, proposal_id=wrong_kind.id, created_by="dev-glossary-owner")
    assert seeded.get_current("isin").version == 1


def test_approved_change_adds_a_version_and_keeps_history(seeded, approved_proposal):
    proposal = approved_proposal(kind="glossary_element", roles=("glossary_owner",))
    changed = {**ISIN, "definition": "ISO 6166 identifier of the instrument. Synthetic test wording."}
    element = seeded.add_version(**changed, proposal_id=proposal.id, created_by="dev-glossary-owner")
    assert (element.version, element.proposal_id) == (2, proposal.id)
    assert seeded.get_current("isin").definition == changed["definition"]
    assert [(e.version, e.created_by) for e in seeded.history("isin")] == [(1, "seed"), (2, "dev-glossary-owner")]
    assert [e.key for e in seeded.list_current()] == ["close_price", "isin"]  # still one row per key


def test_approved_new_element_starts_at_version_1(repo, approved_proposal):
    proposal = approved_proposal(kind="glossary_element", roles=("glossary_owner",))
    element = repo.add_version(**CLOSE, proposal_id=proposal.id, created_by="dev-glossary-owner")
    assert element.version == 1


def test_database_refuses_a_non_seed_element_without_a_proposal(db_session):
    # The BEFORE INSERT approval guard (0004) fires first; ck_glossary_elements_proposal_unless_seed stays
    # as a second guard behind it.
    with pytest.raises(IntegrityError, match="needs an approved glossary_element proposal"):
        db_session.execute(text(
            "INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, created_by) "
            "VALUES ('x_key', 1, 'X', 'pricing', 'string', 'Synthetic.', 'dev-glossary-owner')"
        ))


def test_database_refuses_a_duplicate_version(db_session, seeded):
    with pytest.raises(IntegrityError, match="uq_glossary_elements_key_version"):
        db_session.execute(text(
            "INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, created_by) "
            "VALUES ('isin', 1, 'ISIN', 'security_master', 'string', 'Synthetic.', 'seed')"
        ))
