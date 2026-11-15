"""S2 source list (core/steps/s2_sources.py; phase 3 plan, assumptions 30 to 32).

Outcomes (CLAUDE.md): sources_listed; no_fit. Code finds the catalogue sources in the confirmed scope
that cover a requested item; the agent ranks them; coverage and conditions come from the catalogue.
One source_list proposal per listed source, for the requester. Run with a stand-in agent.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from core.domain.sourcing import SourceDiscoveryOutput
from core.repositories.errors import RequirementNotInStatus
from core.repositories.notifications import NotificationRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirement_scopes import RequirementScopeRepository
from core.repositories.requirements import RequirementRepository
from core.repositories.tables import ProposalLogEntry, ProposalRow
from core.steps.s2_sources import NoFit, SourcesListed, listed_sources
from tests.step_chains import list_sources
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from ports.notify.inapp import InAppNotifier
from tests.conftest import dev_user_for
from tests.stand_in_agents import StandInAgent

CATALOGUE = SourceCatalogue.load(DEFAULT_CATALOGUE)
TEAM = ["dev-backend-team"]
PUBLIC = {"visibility": "public", "provider": "External data providers",
          "where_to_search": ["exchanges", "market_data_vendors", "public_websites"], "reason": "r"}
PRIVATE = {"visibility": "private", "provider": "The firm", "where_to_search": ["internal_systems"], "reason": "r"}


def ranking_agent(order=None, error=None):
    """Ranks the input's candidates by code (or in the given order); proposes like the real agent."""
    from agents.source_discovery import SourceDiscoveryAgent

    def output(agent_input):
        codes = order if order is not None else sorted(c.code for c in agent_input.candidates)
        return {"ranked": [{"source_code": code, "reason": f"Ranked {code}"} for code in codes]}

    return StandInAgent(name="SourceDiscoveryAgent", output_schema=SourceDiscoveryOutput, output=output,
                        error=error, proposals=lambda i, o: [d.model_dump() for d in
                                                             SourceDiscoveryAgent.to_proposals(None, i, o)])


@pytest.fixture
def scoped(db_session, glossary, make_proposal):
    """A requirement with a confirmed scope (status scope_confirmed)."""
    def _make(element_keys=("open_price",), terms=(), scope=PUBLIC, title="open price"):
        req = RequirementRepository(db_session).create(requester_id="dev-requester", title=title,
                                                       element_keys=list(element_keys), terms=list(terms))
        proposal = make_proposal(kind="scope", roles=("requester",), payload=scope, requirement_id=req.id)
        ProposalRepository(db_session).record_decision(proposal.id, user=dev_user_for("requester"),
                                                       decision="approved")
        RequirementScopeRepository(db_session).add_version(
            requirement_id=req.id, proposal_id=proposal.id, confirmed_by="dev-requester",
            visibility=scope["visibility"], provider=scope["provider"], where_to_search=scope["where_to_search"])
        db_session.execute(text("UPDATE requirements SET status = 'scope_confirmed' WHERE id = :id"), {"id": req.id})
        db_session.expire_all()
        return req

    return _make


def run(db_session, req, agent=None):
    return list_sources(db_session, requirement_id=req.id, agent=agent or ranking_agent(), catalogue=CATALOGUE,
                        notifier=InAppNotifier(db_session), backend_team=TEAM)


def status(db_session, req):
    return RequirementRepository(db_session).get(req.id).status


def count(session, table):
    return session.scalar(select(func.count()).select_from(table))


def test_sources_listed_one_proposal_per_source(db_session, scoped):
    req = scoped()
    result = run(db_session, req, ranking_agent(order=["syn_vendor_eod_prices", "syn_backup_eod_prices"]))
    assert isinstance(result, SourcesListed)
    assert [c.source.code for c in result.candidates] == ["syn_vendor_eod_prices", "syn_backup_eod_prices"]
    proposals = ProposalRepository(db_session).list_for_requirement(req.id, "source_list")
    assert len(proposals) == 2 and len({p.proposal_log_id for p in proposals}) == 1
    assert {(p.status, tuple(p.required_approver_roles)) for p in proposals} == {("pending", ("requester",))}
    assert {c.proposal_id for c in result.candidates} == {p.id for p in proposals}
    vendor = result.candidates[0]
    assert vendor.source.conditions == CATALOGUE.get("syn_vendor_eod_prices").conditions
    assert (vendor.coverage.elements, vendor.coverage.terms, vendor.coverage.missing) == (["open_price"], [], [])
    assert vendor.reason == "Ranked syn_vendor_eod_prices"
    assert status(db_session, req) == "awaiting_source_choice"


def test_the_agent_sees_only_the_sources_in_scope_that_cover_a_requested_item(db_session, scoped):
    agent = ranking_agent()
    run(db_session, scoped(), agent)
    assert sorted(c.code for c in agent.inputs[0].candidates) == ["syn_backup_eod_prices", "syn_vendor_eod_prices"]
    assert agent.inputs[0].scope_visibility == "public"


def test_a_ticked_term_is_covered_by_an_other_field(db_session, scoped):
    req = scoped(element_keys=(), terms=("esg score",), title="esg score")
    result = run(db_session, req)
    assert [(c.source.code, c.coverage.terms) for c in result.candidates] == [("syn_open_esg_scores", ["esg score"])]


def test_coverage_names_what_a_source_misses(db_session, scoped):
    req = scoped(element_keys=("open_price", "high_price"))
    result = run(db_session, req)
    backup = next(c for c in result.candidates if c.source.code == "syn_backup_eod_prices")
    assert (backup.coverage.elements, backup.coverage.missing) == (["open_price"], ["high_price"])


def test_a_source_the_agent_invents_is_dropped(db_session, scoped):
    req = scoped()
    result = run(db_session, req, ranking_agent(order=["syn_made_up", "syn_vendor_eod_prices",
                                                       "syn_vendor_eod_prices"]))
    assert [c.source.code for c in result.candidates] == ["syn_vendor_eod_prices"]
    assert len(ProposalRepository(db_session).list_for_requirement(req.id, "source_list")) == 1


def test_no_fit_when_no_source_in_scope_covers_the_request(db_session, scoped):
    req = scoped(element_keys=("yield_to_maturity",), scope=PRIVATE, title="yield to maturity")
    agent = ranking_agent()
    before = count(db_session, ProposalLogEntry)
    result = run(db_session, req, agent)
    assert isinstance(result, NoFit) and result.reason == "nothing_in_scope"
    assert agent.inputs == [] and count(db_session, ProposalLogEntry) == before  # no model call for nothing
    assert ProposalRepository(db_session).list_for_requirement(req.id, "source_list") == []
    assert status(db_session, req) == "no_source_found"
    assert [n.subject for n in NotificationRepository(db_session).list_for("dev-backend-team")] == [
        "No source fits: yield to maturity"]


def test_no_fit_when_the_agent_lists_none(db_session, scoped):
    req = scoped()
    result = run(db_session, req, ranking_agent(order=[]))
    assert isinstance(result, NoFit) and result.reason == "agent_listed_none"
    assert count(db_session, ProposalRow) == 1  # only the scope proposal of the fixture
    assert status(db_session, req) == "no_source_found"


def test_a_released_source_is_not_listed(db_session, scoped):
    from core.repositories.feeds import FeedRepository
    from core.repositories.source_choices import SourceChoiceRepository

    req = scoped()
    feed = FeedRepository(db_session).create_candidate(code="syn_backup_eod_prices", name="Backup",
                                                       source_code="syn_backup_eod_prices", connector="local_file",
                                                       source_ref="files/backup_eod_prices.csv")
    choices = SourceChoiceRepository(db_session)
    choices.record_known(requirement_id=req.id, feed_id=feed.id, source_code="syn_backup_eod_prices")
    choices.record_release(requirement_id=req.id, reason="requester_asked", decided_by="dev-requester")
    agent = ranking_agent()
    run(db_session, req, agent)
    assert [c.code for c in agent.inputs[0].candidates] == ["syn_vendor_eod_prices"]


def test_a_rejected_source_is_not_listed(db_session, scoped):
    """Phase 4 plan, assumption 25: a source whose candidate feed the backend team rejected, for anyone."""
    from core.repositories.feeds import FeedRepository
    from tests.onboarded import rejected_feed

    feed = FeedRepository(db_session).create_candidate(code="syn_backup_eod_prices", name="Backup",
                                                       source_code="syn_backup_eod_prices", connector="local_file",
                                                       source_ref="files/backup_eod_prices.csv")
    rejected_feed(db_session, feed.id)
    req = scoped()
    agent = ranking_agent()
    run(db_session, req, agent)
    assert [c.code for c in agent.inputs[0].candidates] == ["syn_vendor_eod_prices"]


@pytest.mark.parametrize("start", ["awaiting_scope_confirmation", "awaiting_source_choice", "defined"])
def test_list_from_a_wrong_status_calls_no_agent(db_session, scoped, start):
    req = scoped()
    db_session.execute(text("UPDATE requirements SET status = :s WHERE id = :id"), {"s": start, "id": req.id})
    agent = ranking_agent()
    with pytest.raises(RequirementNotInStatus):
        run(db_session, req, agent)
    assert agent.inputs == []


def test_a_status_change_during_the_model_call_logs_it_as_discarded_and_writes_nothing_else(db_session, scoped):
    """Review fix 2: after the call the requirement is locked and its status checked again."""
    req = scoped()
    agent = ranking_agent()
    agent.during_call = lambda session: session.execute(
        text("UPDATE requirements SET status = 'rejected' WHERE id = :id"), {"id": req.id})
    before = (count(db_session, ProposalLogEntry), count(db_session, ProposalRow))
    with pytest.raises(RequirementNotInStatus):
        run(db_session, req, agent)
    # one call row, one discard row pointing at it; no proposal
    from core.repositories.tables import ProposalLogDiscardRow

    assert (count(db_session, ProposalLogEntry), count(db_session, ProposalRow)) == (before[0] + 1, before[1])
    assert count(db_session, ProposalLogDiscardRow) == 1


def test_a_failed_agent_call_leaves_the_status(db_session, scoped):
    req = scoped()
    with pytest.raises(RuntimeError):
        run(db_session, req, ranking_agent(error=RuntimeError("model down")))
    assert status(db_session, req) == "scope_confirmed"


def test_listed_sources_reads_the_pending_proposals_back(db_session, scoped):
    req = scoped()
    result = run(db_session, req)
    assert listed_sources(db_session, req.id, CATALOGUE) == result.candidates
