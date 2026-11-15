"""SourceDiscoveryAgent (S2; phase 3 plan, assumptions 30 and 31) on Agent.run() with the MockProvider."""

from __future__ import annotations

import uuid

import pytest

from agents.source_discovery import SourceDiscoveryAgent
from core.domain.sourcing import CandidateRef, Coverage, SourceDiscoveryInput, SourceDiscoveryOutput
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.step_chains import end_read

CATALOGUE = SourceCatalogue.load(DEFAULT_CATALOGUE)


def _candidate(code, covered):
    entry = CATALOGUE.get(code)
    return CandidateRef(code=code, name=entry.name, provider=entry.provider, description=entry.description,
                        elements=entry.elements, other_fields=entry.other_fields, conditions=entry.conditions,
                        coverage=Coverage(elements=covered, terms=[], missing=[]))


def _input(requirement_id, codes=("syn_vendor_eod_prices", "syn_backup_eod_prices")):
    return SourceDiscoveryInput(requirement_id=requirement_id, requested_elements=["open_price", "close_price"],
                                requested_terms=[], scope_visibility="public",
                                where_to_search=["market_data_vendors"],
                                candidates=[_candidate(c, ["open_price", "close_price"]) for c in codes])


def test_the_agents_declaration():
    assert (SourceDiscoveryAgent.name, SourceDiscoveryAgent.prompt_id, SourceDiscoveryAgent.autonomy,
            SourceDiscoveryAgent.approver_roles) == ("SourceDiscoveryAgent", "source_discovery", "A2", ("requester",))


def test_one_proposal_per_ranked_source_with_the_catalogues_facts(db_session, tmp_path):
    from core.repositories.requirements import RequirementRepository

    req = RequirementRepository(db_session).create(requester_id="dev-requester", title="open price")
    agent = SourceDiscoveryAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))
    end_read(db_session)
    run = agent.run(db_session, _input(req.id))
    # The mock: both cover 2 of 2; the backup declares fewer other fields, so it ranks first.
    assert [p.payload["source_code"] for p in run.proposals] == ["syn_backup_eod_prices", "syn_vendor_eod_prices"]
    assert [p.payload["rank"] for p in run.proposals] == [1, 2]
    vendor = run.proposals[1]
    assert (vendor.kind, vendor.requirement_id, vendor.required_approver_roles) == ("source_list", req.id,
                                                                                    ["requester"])
    assert vendor.payload["conditions"] == CATALOGUE.get("syn_vendor_eod_prices").conditions.model_dump()
    assert vendor.payload["coverage"] == {"elements": ["open_price", "close_price"], "terms": [], "missing": []}


def test_an_invented_or_repeated_source_is_dropped():
    output = SourceDiscoveryOutput.model_validate({"ranked": [
        {"source_code": "syn_nowhere", "reason": "made up"},
        {"source_code": "syn_vendor_eod_prices", "reason": "fits"},
        {"source_code": "syn_vendor_eod_prices", "reason": "again"}]})
    drafts = SourceDiscoveryAgent.to_proposals(None, _input(uuid.uuid4()), output)
    assert [(d.payload["source_code"], d.payload["rank"], d.payload["reason"]) for d in drafts] == [
        ("syn_vendor_eod_prices", 1, "fits")]


@pytest.mark.parametrize("field", ["conditions", "coverage"])
def test_the_models_text_never_sets_coverage_or_conditions(field):
    """The output schema has no place for them: extra fields from a model are refused."""
    with pytest.raises(ValueError):
        SourceDiscoveryOutput.model_validate({"ranked": [{"source_code": "syn_vendor_eod_prices", "reason": "r",
                                                          field: {}}]})
