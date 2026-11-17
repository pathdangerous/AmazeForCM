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


GLENN = "dev-glossary-owner"


def test_new_version_needs_an_approved_glossary_proposal(seeded, db_session, make_proposal, approved_proposal):
    from tests.configured import alias, glossary_proposal

    option = alias("isin", "isin_number")
    pending = glossary_proposal(db_session, [option])
    with pytest.raises(ProposalNotApproved):
        seeded.add_version(option=option, proposal_id=pending.id, created_by=GLENN)
    wrong_kind = approved_proposal(kind="mapping", roles=("data_steward",))
    with pytest.raises(WrongProposalKind):
        seeded.add_version(option=option, proposal_id=wrong_kind.id, created_by=GLENN)
    assert seeded.get_current("isin").version == 1


def test_an_approved_alias_adds_the_next_version_equal_but_for_the_alias(seeded, db_session):
    from tests.configured import alias, approved_element

    element = approved_element(db_session, alias("isin", "isin_number"))
    assert (element.version, element.aliases, element.created_by) == (2, ["isin_code", "isin_number"], GLENN)
    assert (element.name, element.definition) == (ISIN["name"], ISIN["definition"])
    assert [(e.version, e.created_by) for e in seeded.history("isin")] == [(1, "seed"), (2, GLENN)]
    assert [e.key for e in seeded.list_current()] == ["close_price", "isin"]  # still one row per key


def test_an_approved_new_element_starts_at_version_1(repo, db_session):
    from tests.configured import approved_element, new_element

    element = approved_element(db_session, new_element("score_year", data_type="integer"))
    assert (element.key, element.version, element.data_type) == ("score_year", 1, "integer")


def test_only_an_option_the_proposal_offers_is_written(seeded, db_session):
    from core.repositories.errors import NotAProposalOption
    from tests.configured import alias, glossary_proposal

    proposal = glossary_proposal(db_session, [alias("isin", "isin_number")], decision="approved")
    with pytest.raises(NotAProposalOption):
        seeded.add_version(option=alias("isin", "something_else"), proposal_id=proposal.id, created_by=GLENN)


def test_an_alias_the_element_already_has_is_refused_by_name(seeded, db_session):
    """Review finding 1: a named error, before the draft is built (its validator would raise a bare one)."""
    from core.repositories.errors import AliasAlreadyOnElement
    from tests.configured import alias, glossary_proposal

    option = alias("isin", "isin_code")  # a seed alias of isin
    proposal = glossary_proposal(db_session, [option], decision="approved")
    with pytest.raises(AliasAlreadyOnElement):
        seeded.add_version(option=option, proposal_id=proposal.id, created_by=GLENN)


def test_a_new_element_whose_key_exists_is_phrase_taken(seeded, db_session):
    from core.repositories.errors import PhraseTaken
    from tests.configured import glossary_proposal, new_element

    option = new_element("isin")
    proposal = glossary_proposal(db_session, [option], decision="approved")
    with pytest.raises(PhraseTaken):
        seeded.add_version(option=option, proposal_id=proposal.id, created_by=GLENN)


def test_phrases_taken_names_each_phrase_another_element_owns(seeded):
    from core.domain.glossary import GlossaryElementDraft

    draft = GlossaryElementDraft(key="closing", name="Close Price", domain="pricing", data_type="decimal",
                                 definition="d", aliases=["isin_code", "fresh_alias"])
    assert seeded.phrases_taken(draft) == [("Close Price", "close_price"), ("isin_code", "isin")]
    own = GlossaryElementDraft(**{**ISIN, "aliases": ["isin_code", "isin_number"]})
    assert seeded.phrases_taken(own) == []  # an element's own phrases are not taken


def test_the_glossary_lock_is_an_advisory_lock_held_to_the_end_of_the_transaction(repo, db_session):
    repo.lock()
    held = db_session.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                                   "AND pid = pg_backend_pid()")).scalar_one()
    assert held == 1


def test_database_refuses_a_non_seed_element_without_a_proposal(db_session):
    # The BEFORE INSERT approval guard (0004) fires first; ck_glossary_elements_proposal_unless_seed stays
    # as a second guard behind it.
    with pytest.raises(IntegrityError, match="needs an approved glossary_element proposal"):
        db_session.execute(text(
            "INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, created_by) "
            "VALUES ('x_key', 1, 'X', 'pricing', 'string', 'Synthetic.', 'dev-glossary-owner')"
        ))


def test_database_refuses_a_duplicate_version(db_session, seeded):
    """From 0016 trg_glossary_elements_b_option refuses a seed row for an existing key first; with that
    trigger disabled (in a savepoint), uq_glossary_elements_key_version still refuses it."""
    duplicate = text("INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, created_by) "
                     "VALUES ('isin', 1, 'ISIN', 'security_master', 'string', 'Synthetic.', 'seed')")
    with pytest.raises(IntegrityError, match="a seed row is version 1 of a new key"):
        with db_session.begin_nested():
            db_session.execute(duplicate)
    with db_session.begin_nested() as behind:
        db_session.execute(text("ALTER TABLE glossary_elements DISABLE TRIGGER trg_glossary_elements_b_option"))
        with pytest.raises(IntegrityError, match="uq_glossary_elements_key_version"):
            with db_session.begin_nested():
                db_session.execute(duplicate)
        behind.rollback()
