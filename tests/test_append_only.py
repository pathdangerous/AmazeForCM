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
    from tests.configured import approved_mapping

    feed = _feed_with_glossary(db_session, request)
    return "mappings", approved_mapping(db_session, feed.id, "Probe", "append_only_probe").id


def _rules_row(db_session, request):
    """From 0018 a rule matches its approved proposal (same feed, key, SQL, description) on a registered feed."""
    from core.repositories.configuration import RuleRepository
    from tests.configured import a_registered_feed
    from tests.test_db_rule_guards import rule_proposal

    feed = a_registered_feed(db_session, "append_only_rules")
    payload = {"rule_key": "probe", "sql": "SELECT 1", "description": "Synthetic."}
    proposal = rule_proposal(db_session, feed, payload=payload, decision="approved")
    rule = RuleRepository(db_session).add_version(
        feed_id=feed, rule_key="probe", sql="SELECT 1", description="Synthetic.", proposal_id=proposal.id
    )
    return "rules", rule.id


# ---- phase 2 (migration 0006)


def _product(db_session, request):
    from core.repositories.products import ProductRepository

    _glossary_row(db_session, request)
    return ProductRepository(db_session).create(
        code="append_only_product", name="Append-only product", domain="pricing", owner_id="dev-data-owner",
        visibility="restricted", element_keys=["append_only_probe"],
    )


def _product_elements_row(db_session, request):
    product = _product(db_session, request)
    row_id = db_session.execute(
        text("SELECT id FROM product_elements WHERE product_id = :p"), {"p": product.id}
    ).scalar_one()
    return "product_elements", row_id


def _product_grants_row(db_session, request):
    from core.repositories.grants import ProductGrantRepository

    product = _product(db_session, request)
    event = ProductGrantRepository(db_session).record(
        product_id=product.id, subject_type="role", subject="requester", action="grant", decided_by="dev-data-owner"
    )
    return "product_grants", event.id


def _requirement(db_session, request):
    from core.repositories.requirements import RequirementRepository

    _glossary_row(db_session, request)
    return RequirementRepository(db_session).create(
        requester_id="dev-requester", title="Append-only requirement", element_keys=["append_only_probe"],
        terms=["synthetic term"],
    )


def _requirement_elements_row(db_session, request):
    requirement = _requirement(db_session, request)
    row_id = db_session.execute(
        text("SELECT id FROM requirement_elements WHERE requirement_id = :r"), {"r": requirement.id}
    ).scalar_one()
    return "requirement_elements", row_id


def _requirement_terms_row(db_session, request):
    requirement = _requirement(db_session, request)
    row_id = db_session.execute(
        text("SELECT id FROM requirement_terms WHERE requirement_id = :r"), {"r": requirement.id}
    ).scalar_one()
    return "requirement_terms", row_id


def _consumer_requests_row(db_session, request):
    from core.repositories.consumer_requests import ConsumerRequestRepository

    requirement = _requirement(db_session, request)
    recorded = ConsumerRequestRepository(db_session).record(
        requester_id="dev-requester", query="synthetic term", element_keys=["append_only_probe"],
        terms=["synthetic term"], outcome="raise_requirement", requirement_id=requirement.id,
        result={"outcome": "raise_requirement"},
    )
    return "consumer_requests", recorded.id


# ---- phase 3a (migration 0008)


def _source_feed(db_session):
    from core.repositories.feeds import FeedRepository

    return FeedRepository(db_session).create_candidate(
        code="append_only_source", name="Append-only source", source_code="append_only_source",
        connector="local_file", source_ref="files/append_only.csv",
    )


def _source_choices_row(db_session, request):
    from core.repositories.requirements import RequirementRepository
    from core.repositories.source_choices import SourceChoiceRepository

    requirement = RequirementRepository(db_session).create(requester_id="dev-requester", title="Append-only")
    choice = SourceChoiceRepository(db_session).record_known(
        requirement_id=requirement.id, feed_id=_source_feed(db_session).id, source_code="append_only_source"
    )
    return "source_choices", choice.id


def _source_fetches_row(db_session, request):
    from core.repositories.source_fetches import SourceFetchRepository

    row = SourceFetchRepository(db_session).record(
        feed_id=_source_feed(db_session).id, outcome="source_down", detail="synthetic"
    )
    return "source_fetches", row.id


def _requirement_scopes_row(db_session, request):
    from core.repositories.proposals import ProposalRepository
    from core.repositories.requirement_scopes import RequirementScopeRepository
    from core.repositories.requirements import RequirementRepository
    from tests.conftest import dev_user_for

    requirement = RequirementRepository(db_session).create(requester_id="dev-requester", title="Append-only")
    make_proposal = request.getfixturevalue("make_proposal")
    proposal = make_proposal(kind="scope", roles=("requester",), requirement_id=requirement.id,
                             payload={"visibility": "public", "provider": "P", "where_to_search": ["exchanges"],
                                      "reason": "r"})
    ProposalRepository(db_session).record_decision(proposal.id, user=dev_user_for("requester"), decision="approved")
    scope = RequirementScopeRepository(db_session).add_version(
        requirement_id=requirement.id, visibility="public", provider="P", where_to_search=["exchanges"],
        proposal_id=proposal.id, confirmed_by="dev-requester",
    )
    return "requirement_scopes", scope.id


def _proposal_log_discards_row(db_session, request):
    from core.proposal_log import ProposalLog

    log_name, log_id = _proposal_log_row(db_session, request)
    return "proposal_log_discards", ProposalLog(db_session).discard(proposal_log_id=log_id, reason="synthetic").id


# ---- phase 4 (migration 0014)


def _phase_4_feed(db_session):
    from core.repositories.feeds import FeedRepository

    return FeedRepository(db_session).create_candidate(code="append_only_feed", name="Append-only feed").id


def _feed_profiles_row(db_session, request):
    from tests.onboarded import profiled

    return "feed_profiles", profiled(db_session, _phase_4_feed(db_session)).id


def _feed_signoffs_row(db_session, request):
    from tests.onboarded import signed_off

    return "feed_signoffs", signed_off(db_session, _phase_4_feed(db_session)).id


def _feed_registrations_row(db_session, request):
    from tests.onboarded import registration

    return "feed_registrations", registration(db_session, _phase_4_feed(db_session)).id


# ---- phase 6 (migration 0018)


def _phase_6_feed(db_session):
    from tests.configured import a_registered_feed

    return a_registered_feed(db_session, "append_only_phase_6")


def _feed_go_lives_row(db_session, request):
    import uuid
    from types import SimpleNamespace

    from tests.test_db_go_live_guards import GO_LIVE, go_live_params, summary_proposal
    from tests.test_db_load_guards import loaded_load

    feed = _phase_6_feed(db_session)
    world = SimpleNamespace(feed=feed, mappings=[uuid.uuid4()], rules=[], load=loaded_load(db_session, feed))
    proposal = summary_proposal(db_session, world)
    return "feed_go_lives", db_session.execute(text(GO_LIVE), go_live_params(world, proposal)).scalar_one()


def _load_stages_row(db_session, request):
    from tests.test_db_load_guards import add_stage, new_load

    load = new_load(db_session, _phase_6_feed(db_session))
    add_stage(db_session, load, "raw")
    return "load_stages", db_session.execute(text("SELECT id FROM load_stages WHERE load_id = :l"),
                                             {"l": load}).scalar_one()


def _standard_records_row(db_session, request):
    from tests.test_db_load_guards import RECORD, new_load

    feed = _phase_6_feed(db_session)
    load = new_load(db_session, feed)
    db_session.execute(text(RECORD), {"load": load, "feed": feed, "row": 1, "vals": "{}"})
    return "standard_records", db_session.execute(text("SELECT id FROM standard_records WHERE load_id = :l"),
                                                  {"l": load}).scalar_one()


# ---- phase 7 (migration 0019)


def _load_certifications_row(db_session, request):
    from tests.configured import a_registered_feed, go_live
    from tests.test_db_arrival_guards import CERT, cert_params, scheduled_loaded

    feed = a_registered_feed(db_session, "append_only_phase_7")
    load = scheduled_loaded(db_session, (feed, go_live(db_session, feed).id))
    db_session.execute(text(CERT), cert_params(load, feed))
    return "load_certifications", db_session.execute(text("SELECT id FROM load_certifications WHERE load_id = :l"),
                                                     {"l": load}).scalar_one()


ROWS = {
    "load_certifications": _load_certifications_row,
    "feed_go_lives": _feed_go_lives_row,
    "load_stages": _load_stages_row,
    "standard_records": _standard_records_row,
    "feed_profiles": _feed_profiles_row,
    "feed_signoffs": _feed_signoffs_row,
    "feed_registrations": _feed_registrations_row,
    "proposal_log": _proposal_log_row,
    "proposal_log_discards": _proposal_log_discards_row,
    "requirement_scopes": _requirement_scopes_row,
    "approvals": _approvals_row,
    "glossary_elements": _glossary_row,
    "mappings": _mappings_row,
    "rules": _rules_row,
    "product_elements": _product_elements_row,
    "product_grants": _product_grants_row,
    "requirement_elements": _requirement_elements_row,
    "requirement_terms": _requirement_terms_row,
    "consumer_requests": _consumer_requests_row,
    "source_choices": _source_choices_row,
    "source_fetches": _source_fetches_row,
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
