"""Append-only tables (CLAUDE.md rule 7, and decisions never overwritten): the database itself refuses
UPDATE and DELETE, even for hand-written SQL. Each later task adds its table to ROWS."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import dev_user_for


def _proposal_log_row(db_session, request):
    return "proposal_log", request.getfixturevalue("logged_call").id


def _approvals_row(db_session, request):
    from core.repositories.proposals import ProposalRepository

    proposal = request.getfixturevalue("make_proposal")()
    approval = ProposalRepository(db_session).record_decision(
        proposal.id, user=dev_user_for("data_steward"), decision="approved"
    )
    return "approvals", approval.id


def _glossary_row(db_session, request):
    from core.repositories.glossary import GlossaryRepository

    repo = GlossaryRepository(db_session)
    repo.add_seed_element(key="append_only_probe", name="Probe", domain="pricing", data_type="string",
                          definition="Synthetic element for the append-only test.", aliases=[])
    return "glossary_elements", repo.get_current("append_only_probe").id


def _feed_with_glossary(db_session, request):
    from core.repositories.feeds import FeedRepository

    _glossary_row(db_session, request)
    return FeedRepository(db_session).create_candidate(code="append_only_feed", name="Append-only feed")


def _mappings_row(db_session, request):
    from core.repositories.configuration import MappingRepository

    feed = _feed_with_glossary(db_session, request)
    proposal = request.getfixturevalue("approved_proposal")(kind="mapping")
    mapping = MappingRepository(db_session).add_version(
        feed_id=feed.id, source_field="Probe", element_key="append_only_probe", proposal_id=proposal.id
    )
    return "mappings", mapping.id


def _rules_row(db_session, request):
    from core.repositories.configuration import RuleRepository

    feed = _feed_with_glossary(db_session, request)
    proposal = request.getfixturevalue("approved_proposal")(kind="rule", roles=("data_steward", "checker"))
    rule = RuleRepository(db_session).add_version(
        feed_id=feed.id, rule_key="probe", sql="SELECT 1", description="Synthetic.", proposal_id=proposal.id
    )
    return "rules", rule.id


ROWS = {
    "proposal_log": _proposal_log_row,
    "approvals": _approvals_row,
    "glossary_elements": _glossary_row,
    "mappings": _mappings_row,
    "rules": _rules_row,
}


@pytest.mark.parametrize("table", sorted(ROWS))
def test_update_is_refused(db_session, request, table):
    name, row_id = ROWS[table](db_session, request)
    with pytest.raises(DBAPIError, match=f"{name} is append-only"):
        db_session.execute(text(f"UPDATE {name} SET created_at = created_at WHERE id = :id"), {"id": row_id})


@pytest.mark.parametrize("table", sorted(ROWS))
def test_truncate_is_refused(db_session, request, table):
    """TRUNCATE skips row-level triggers, so each table also has a statement-level BEFORE TRUNCATE trigger."""
    name, _ = ROWS[table](db_session, request)
    with pytest.raises(DBAPIError, match=f"{name} is append-only: TRUNCATE refused"):
        db_session.execute(text(f"TRUNCATE {name} CASCADE"))


@pytest.mark.parametrize("table", sorted(ROWS))
def test_delete_is_refused(db_session, request, table):
    name, row_id = ROWS[table](db_session, request)
    with pytest.raises(DBAPIError, match=f"{name} is append-only"):
        db_session.execute(text(f"DELETE FROM {name} WHERE id = :id"), {"id": row_id})
