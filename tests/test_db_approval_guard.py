"""Rule 1 in the database: a row in mappings, rules or glossary_elements (except the seed) can only be
inserted for a proposal that is approved, of the matching kind, and has at least one 'approved'
decision in approvals. Raw SQL bypasses the repositories on purpose."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository

STEWARD_THEN_CHECKER = ("data_steward", "checker")

# table -> (proposal kind, roles that approve it, raw insert using :feed and :proposal)
TABLES = {
    "mappings": (
        "mapping", ("data_steward",),
        "INSERT INTO mappings (feed_id, source_field, element_key, version, proposal_id) "
        "VALUES (:feed, 'GuardField', 'close_price', 1, :proposal)",
    ),
    "rules": (
        "rule", STEWARD_THEN_CHECKER,
        "INSERT INTO rules (feed_id, rule_key, version, sql, description, proposal_id) "
        "VALUES (:feed, 'guard_rule', 1, 'SELECT 1', 'Synthetic.', :proposal)",
    ),
    "glossary_elements": (
        "glossary_element", ("glossary_owner",),
        "INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, created_by, proposal_id) "
        "VALUES ('guard_element', 1, 'Guard', 'pricing', 'string', 'Synthetic.', 'dev-glossary-owner', :proposal)",
    ),
}
OTHER_KIND = {"mapping": ("rule", STEWARD_THEN_CHECKER), "rule": ("mapping", ("data_steward",)),
              "glossary_element": ("mapping", ("data_steward",))}


@pytest.fixture
def feed(db_session):
    GlossaryRepository(db_session).add_seed_element(
        key="close_price", name="Close price", domain="pricing", data_type="decimal",
        definition="Synthetic definition.", aliases=[],
    )
    return FeedRepository(db_session).create_candidate(code="guard_feed", name="Guard feed")


def _insert(db_session, table, feed, proposal_id):
    db_session.execute(text(TABLES[table][2]), {"feed": feed.id, "proposal": proposal_id})


@pytest.mark.parametrize("table", sorted(TABLES))
def test_accepts_a_properly_approved_proposal(db_session, feed, approved_proposal, table):
    kind, roles, _ = TABLES[table]
    _insert(db_session, table, feed, approved_proposal(kind=kind, roles=roles).id)
    assert db_session.execute(text(f"SELECT count(*) FROM {table} WHERE proposal_id IS NOT NULL")).scalar_one() == 1


@pytest.mark.parametrize("table", sorted(TABLES))
def test_refuses_a_pending_proposal(db_session, feed, make_proposal, table):
    kind, roles, _ = TABLES[table]
    with pytest.raises(IntegrityError, match="is pending, not approved"):
        _insert(db_session, table, feed, make_proposal(kind=kind, roles=roles).id)


@pytest.mark.parametrize("table", sorted(TABLES))
def test_refuses_an_approved_proposal_of_another_kind(db_session, feed, approved_proposal, table):
    kind, _, _ = TABLES[table]
    other_kind, other_roles = OTHER_KIND[kind]
    with pytest.raises(IntegrityError, match=f"is a {other_kind} proposal, not {kind}"):
        _insert(db_session, table, feed, approved_proposal(kind=other_kind, roles=other_roles).id)


@pytest.mark.parametrize("table", sorted(TABLES))
def test_refuses_a_proposal_marked_approved_without_an_approval(db_session, feed, make_proposal, table):
    """A status forged by hand (UPDATE proposals SET status = 'approved') is not enough."""
    kind, roles, _ = TABLES[table]
    proposal = make_proposal(kind=kind, roles=roles)
    db_session.execute(text("UPDATE proposals SET status = 'approved', decided_at = now() WHERE id = :id"),
                       {"id": proposal.id})
    with pytest.raises(IntegrityError, match="has no approved decision"):
        _insert(db_session, table, feed, proposal.id)


def test_seed_glossary_element_needs_no_proposal(db_session):
    db_session.execute(text(
        "INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, created_by) "
        "VALUES ('seed_probe', 1, 'Seed probe', 'pricing', 'string', 'Synthetic.', 'seed')"
    ))
    assert db_session.execute(text("SELECT count(*) FROM glossary_elements WHERE key = 'seed_probe'")).scalar_one() == 1
