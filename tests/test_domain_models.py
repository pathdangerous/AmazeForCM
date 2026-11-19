"""Domain models validate what the database cannot say simply. Each task adds its models here."""

import uuid

import pytest
from pydantic import ValidationError

from core.domain.glossary import DATA_TYPES, GlossaryElementDraft
from core.domain.proposal import PROPOSAL_KINDS, ProposalDraft

# ---- Proposal (task 4)


def test_proposal_kinds_are_the_eight_agent_outputs_with_an_approver():
    assert set(PROPOSAL_KINDS) == {
        "scope", "source_list", "profile_summary", "glossary_element", "mapping", "rule", "review_summary",
        "extracted_table",  # phase 7.8, task 2.3
        "transformation",  # phase 7.8, task 4.1
    }


def test_proposal_draft_accepts_valid_input():
    draft = ProposalDraft(kind="rule", payload={"sql": "SELECT 1"}, autonomy="A2",
                          required_approver_roles=["data_steward", "checker"])
    assert draft.required_approver_roles == ["data_steward", "checker"]


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "discovery"},
        {"autonomy": "A3"},
        {"required_approver_roles": []},
        {"required_approver_roles": ["superuser"]},
        {"required_approver_roles": ["data_steward", "data_steward"]},  # one role cannot decide two steps
    ],
)
def test_proposal_draft_rejects(bad):
    fields = {"kind": "rule", "payload": {}, "autonomy": "A2", "required_approver_roles": ["data_steward"]}
    with pytest.raises(ValidationError):
        ProposalDraft(**{**fields, **bad})


# ---- GlossaryElement (task 5)

ELEMENT = {
    "key": "close_price", "name": "Close price", "domain": "pricing", "data_type": "decimal",
    "definition": "Synthetic definition.", "aliases": ["closing_price"],
}


def test_data_types():
    assert set(DATA_TYPES) == {"string", "integer", "decimal", "date", "datetime", "boolean"}


def test_glossary_draft_accepts_valid_input():
    assert GlossaryElementDraft(**ELEMENT).aliases == ["closing_price"]


@pytest.mark.parametrize(
    "bad",
    [
        {"key": "Close Price"},
        {"key": "1close"},
        {"domain": "Pricing"},
        {"data_type": "money"},
        {"name": " "},
        {"definition": ""},
        {"aliases": ["Closing Price"]},
        {"aliases": ["closing_price", "closing_price"]},
        {"aliases": ["close_price"]},  # an alias cannot repeat the key
    ],
)
def test_glossary_draft_rejects(bad):
    with pytest.raises(ValidationError):
        GlossaryElementDraft(**{**ELEMENT, **bad})


# ---- Requirement, Feed, Mapping, Rule, Load, Product (tasks 7 to 9)

from core.domain.configuration import MappingDraft, RuleDraft  # noqa: E402
from core.domain.feed import FEED_STAGES, FeedDraft  # noqa: E402
from core.domain.load import (  # noqa: E402
    ARRIVAL_OUTCOMES, CERTIFICATION_OUTCOMES, LOAD_STATUSES, LoadCertificationDraft, LoadDraft)
from core.domain.product import ProductDraft  # noqa: E402
from core.domain.requirement import REQUIREMENT_STATUSES, RequirementDraft  # noqa: E402


def test_status_lists_are_the_names_in_claude_md():
    # phase 3 (migration 0008) adds source_selected
    assert REQUIREMENT_STATUSES == (
        "defined", "onboarding", "ready", "access_restricted", "rejected", "source_selected",  # phases 1, 3a
        "awaiting_scope_confirmation", "scope_not_confirmed", "scope_confirmed", "awaiting_source_choice",
        "no_source_found",  # phase 3b, migration 0009
    )
    assert FEED_STAGES == ("candidate", "registered", "rejected")
    assert LOAD_STATUSES == ("received", "loaded", "held", "failed_rolled_back", "released",  # loaded: 0018
                             "quarantined", "abandoned")  # 0019
    assert ARRIVAL_OUTCOMES == ("on_time_complete", "resent_quarantined", "additive_parked", "breaking_held")
    assert CERTIFICATION_OUTCOMES == ("released", "held_consumers_warned")


CERTIFICATION = {"load_id": uuid.uuid4(), "feed_id": uuid.uuid4(), "rows": 10, "flagged_rows": 1, "trust_score": 90,
                 "threshold": 90, "outcome": "released"}


@pytest.mark.parametrize(
    ("model", "valid", "bad"),
    [
        (RequirementDraft, {"requester_id": "dev-requester", "title": "T"}, {"title": " "}),
        (FeedDraft, {"code": "feed_a", "name": "Feed A"}, {"code": "Feed-A"}),
        (FeedDraft, {"code": "feed_a", "name": "Feed A"}, {"owner_id": " "}),
        (MappingDraft, {"source_field": "ClosePx", "element_key": "close_price"}, {"element_key": "Close"}),
        (MappingDraft, {"source_field": "ClosePx", "element_key": "close_price"}, {"source_field": ""}),
        (RuleDraft, {"rule_key": "r", "sql": "SELECT 1", "description": "D"}, {"sql": " "}),
        (LoadDraft, {"file_ref": "local://raw/x.csv", "purpose": "go_live_test"}, {"file_ref": ""}),
        (LoadDraft, {"file_ref": "local://raw/x.csv", "purpose": "go_live_test"}, {"purpose": "other"}),
        (LoadDraft, {"file_ref": "local://raw/x.csv", "purpose": "go_live_test"}, {"purpose": "scheduled"}),  # no go-live
        (LoadCertificationDraft, CERTIFICATION, {"outcome": "ok"}),  # 0019
        (LoadCertificationDraft, CERTIFICATION, {"flagged_rows": 11}),  # more flagged than rows
        (LoadCertificationDraft, CERTIFICATION, {"trust_score": 100.1}),
        (LoadCertificationDraft, CERTIFICATION, {"threshold": 0}),
    ],
)
def test_drafts_validate(model, valid, bad):
    model(**valid)
    with pytest.raises(ValidationError):
        model(**{**valid, **bad})


# ---- Product: owner, visibility and elements (phase 2, task 1)

from core.domain.product import VISIBILITIES  # noqa: E402

PRODUCT = {"code": "p", "name": "P", "domain": "pricing", "owner_id": "dev-data-owner", "visibility": "open",
           "element_keys": ["close_price", "price_date"]}


def test_visibilities():
    assert VISIBILITIES == ("open", "restricted")


def test_product_draft_accepts_valid_input():
    assert ProductDraft(**PRODUCT).element_keys == ["close_price", "price_date"]


@pytest.mark.parametrize(
    "bad",
    [
        {"domain": "Pricing"},
        {"owner_id": " "},
        {"visibility": "secret"},
        {"element_keys": []},
        {"element_keys": ["close_price", "close_price"]},
        {"element_keys": ["Close Price"]},
    ],
)
def test_product_draft_rejects(bad):
    with pytest.raises(ValidationError):
        ProductDraft(**{**PRODUCT, **bad})


# ---- Grant events and the access decision (phase 2, task 2)

from core.domain.entitlement import ACCESS_REASONS, Access, GrantDraft  # noqa: E402

GRANT = {"subject_type": "role", "subject": "requester", "action": "grant", "decided_by": "dev-data-owner"}


def test_access_reasons_are_the_lines_of_the_rule():
    assert ACCESS_REASONS == ("open", "owner", "user_grant", "user_revoke", "role_grant", "no_grant")
    with pytest.raises(ValidationError):
        Access(entitled=True, reason="because")


def test_grant_draft_accepts_a_user_or_a_role():
    assert GrantDraft(**GRANT).subject == "requester"
    assert GrantDraft(**{**GRANT, "subject_type": "user", "subject": "dev-requester"}).subject == "dev-requester"


@pytest.mark.parametrize(
    "bad",
    [{"subject_type": "group"}, {"action": "deny"}, {"subject": "superuser"}, {"subject": " "}, {"decided_by": " "}],
)
def test_grant_draft_rejects(bad):
    with pytest.raises(ValidationError):
        GrantDraft(**{**GRANT, **bad})


# ---- Requirement elements and terms, and the request log (phase 2, task 3)

from core.domain.consumer_request import ROUTING_OUTCOMES, ConsumerRequestDraft  # noqa: E402

REQUEST = {"requester_id": "dev-requester", "query": "close price", "element_keys": ["close_price"],
           "outcome": "reuse", "result": {"outcome": "reuse"}}


def test_routing_outcomes_are_the_names_in_claude_md():
    assert ROUTING_OUTCOMES == ("reuse", "compose", "create_product", "raise_requirement")


def test_requirement_draft_takes_elements_and_terms():
    draft = RequirementDraft(requester_id="dev-requester", title="T", element_keys=["volume"], terms=["esg score"])
    assert (draft.element_keys, draft.terms) == (["volume"], ["esg score"])
    assert RequirementDraft(requester_id="dev-requester", title="T").element_keys == []


@pytest.mark.parametrize(
    "bad",
    [{"element_keys": ["volume", "volume"]}, {"element_keys": ["Volume"]}, {"terms": [""]}, {"terms": ["a", "a"]}],
)
def test_requirement_draft_rejects(bad):
    with pytest.raises(ValidationError):
        RequirementDraft(**{"requester_id": "dev-requester", "title": "T", **bad})


def test_consumer_request_draft_accepts_valid_input():
    assert ConsumerRequestDraft(**REQUEST).purpose == ""
    with_terms = ConsumerRequestDraft(**{**REQUEST, "element_keys": [], "terms": ["esg score"]})
    assert with_terms.terms == ["esg score"]


@pytest.mark.parametrize(
    "bad",
    [
        {"outcome": "found"},
        {"element_keys": []},  # with no terms either: nothing was requested
        {"query": " "},
        {"requirement_id": "5f0c3a2e-0000-4000-8000-000000000000"},  # only raise_requirement links a requirement
        {"outcome": "raise_requirement"},  # and it always does
    ],
)
def test_consumer_request_draft_rejects(bad):
    with pytest.raises(ValidationError):
        ConsumerRequestDraft(**{**REQUEST, **bad})


# ---- phase 3a, task 1: transitions, feed sources, fetches

from core.domain.requirement import REQUIREMENT_TRANSITIONS  # noqa: E402
from core.domain.sourcing import SourceFetchDraft  # noqa: E402


def test_requirement_statuses_and_transitions_are_the_plans():
    """Phase 3 plan, section 5.1: the moves that change a status (3a and 3b), phase 4's two, and, from phase 7
    (assumption 27), the two step 20 makes; nothing else."""
    assert REQUIREMENT_TRANSITIONS == frozenset({
        ("defined", "source_selected"), ("source_selected", "onboarding"),  # 3a
        ("defined", "awaiting_scope_confirmation"),  # S1
        ("awaiting_scope_confirmation", "scope_confirmed"),
        ("awaiting_scope_confirmation", "scope_not_confirmed"),
        ("scope_not_confirmed", "awaiting_scope_confirmation"),
        ("no_source_found", "awaiting_scope_confirmation"),
        ("scope_confirmed", "awaiting_source_choice"), ("scope_confirmed", "no_source_found"),  # S2
        ("awaiting_source_choice", "source_selected"), ("awaiting_source_choice", "no_source_found"),  # S3
        ("source_selected", "awaiting_source_choice"), ("source_selected", "no_source_found"),  # release
        ("defined", "no_source_found"), ("scope_not_confirmed", "no_source_found"),  # S1: no channel (review fix 5)
        ("onboarding", "awaiting_source_choice"), ("onboarding", "no_source_found"),  # phase 4: 03 rejected
        ("onboarding", "ready"), ("onboarding", "access_restricted"),  # phase 7 assumption 27: step 20 -> created
    })
    assert len(REQUIREMENT_TRANSITIONS) == 19  # was 17
    assert {s for move in REQUIREMENT_TRANSITIONS for s in move} <= set(REQUIREMENT_STATUSES)


def test_release_reasons_and_the_phase_4_outcome_lists():
    from core.domain.profiling import PROFILE_OUTCOMES, SIGNOFF_OUTCOMES
    from core.domain.sourcing import RELEASE_REASONS

    assert RELEASE_REASONS == ("source_down_n_days", "requester_asked", "candidate_rejected")
    assert PROFILE_OUTCOMES == ("profiled", "spec_only_low_confidence")
    assert SIGNOFF_OUTCOMES == ("meets", "meets_with_gaps", "rejected")


@pytest.mark.parametrize(
    ("outcome", "gaps", "reasons", "ok"),
    [("meets", [], [], True), ("meets", ["g"], [], False), ("meets", [], ["r"], False),
     ("meets_with_gaps", ["g"], [], True), ("meets_with_gaps", [], [], False), ("meets_with_gaps", ["g"], ["r"], False),
     ("rejected", [], ["r"], True), ("rejected", [], [], False), ("rejected", ["g"], ["r"], False),
     ("rejected", [], [" "], False)],
)
def test_a_signoff_draft_fits_gaps_and_reasons_to_its_outcome(outcome, gaps, reasons, ok):
    import uuid

    from core.domain.profiling import FeedSignoffDraft

    fields = {"feed_id": uuid.uuid4(), "profile_id": uuid.uuid4(), "proposal_id": uuid.uuid4(), "outcome": outcome,
              "gaps": gaps, "reasons": reasons, "signed_off_by": "dev-backend-team"}
    if ok:
        FeedSignoffDraft(**fields)
    else:
        with pytest.raises(ValidationError):
            FeedSignoffDraft(**fields)


FEED = {"code": "syn_exchange_volumes", "name": "Exchange volumes", "source_code": "syn_exchange_volumes",
        "connector": "local_file", "source_ref": "files/exchange_volumes.csv"}


def test_feed_draft_takes_a_source():
    assert FeedDraft(**FEED).connector == "local_file"
    assert FeedDraft(code="feed_a", name="Feed A").source_code is None


@pytest.mark.parametrize(
    "bad",
    [
        {"connector": "ftp"},
        {"connector": None},  # source code without a connector
        {"source_code": None},  # connector without a source code
        {"source_ref": None},  # a connector needs a reference
        {"source_code": "Syn Exchange"},
    ],
)
def test_feed_draft_rejects_an_incomplete_source(bad):
    with pytest.raises(ValidationError):
        FeedDraft(**{**FEED, **bad})


@pytest.mark.parametrize(
    ("valid", "bad"),
    [
        ({"outcome": "fetched", "blob_ref": "raw/f/2026-11-14/x/f.csv", "size_bytes": 10}, {"size_bytes": 0}),
        ({"outcome": "fetched", "blob_ref": "raw/f/2026-11-14/x/f.csv", "size_bytes": 10}, {"blob_ref": None}),
        ({"outcome": "fetched", "blob_ref": "raw/f/2026-11-14/x/f.csv", "size_bytes": 10}, {"detail": "why"}),
        ({"outcome": "source_down", "detail": "not found"}, {"detail": " "}),
        ({"outcome": "source_down", "detail": "not found"}, {"blob_ref": "raw/x"}),
        ({"outcome": "source_down", "detail": "not found"}, {"outcome": "lost"}),
    ],
)
def test_source_fetch_draft_validates(valid, bad):
    SourceFetchDraft(**valid)
    with pytest.raises(ValidationError):
        SourceFetchDraft(**{**valid, **bad})


# ---- phase 5, task 4: glossary runs and the two proposal payloads

from core.domain.mapping import (  # noqa: E402
    GLOSSARY_OUTCOMES,
    GLOSSARY_RUN_REASONS,
    GLOSSARY_RUN_STATUSES,
    ElementOption,
    GlossaryProposalPayload,
    MappingProposalPayload,
)

NEW_ELEMENT = {"key": "score_year", "name": "Score year", "domain": "pricing", "data_type": "integer",
               "definition": "Synthetic."}


def test_the_phase_5_lists_are_the_plans():
    assert GLOSSARY_OUTCOMES == ("proposed", "duplicate_alias", "conflict_to_owner")
    assert GLOSSARY_RUN_REASONS == ("registered", "requested", "sent_back", "structure_change")  # the fourth: 0019
    assert GLOSSARY_RUN_STATUSES == ("queued", "running", "done", "failed")


@pytest.mark.parametrize("bad", [
    {"kind": "new_element", "key": "score_year"},  # no element
    {"kind": "new_element", "key": "other_key", "element": NEW_ELEMENT},  # element under another key
    {"kind": "new_element", "key": "score_year", "element": NEW_ELEMENT, "alias": "x"},
    {"kind": "alias", "key": "isin"},  # no alias
    {"kind": "alias", "key": "isin", "alias": "Bad Alias"},
    {"kind": "alias", "key": "isin", "alias": "x", "element": NEW_ELEMENT},
])
def test_an_element_option_has_one_shape_per_kind(bad):
    ElementOption(kind="new_element", key="score_year", element=NEW_ELEMENT)
    ElementOption(kind="alias", key="isin", alias="isin_number")
    with pytest.raises(ValidationError):
        ElementOption(**bad)


def _glossary_payload(**more):
    option = {"kind": "new_element", "key": "score_year", "element": NEW_ELEMENT}
    return {"source_field": "score_year", "outcome": "proposed", "options": [option], "recommended": 0,
            "reason": "r", "run_id": "00000000-0000-0000-0000-000000000000", **more}


@pytest.mark.parametrize("bad", [
    {"recommended": None},  # proposed recommends its one option
    {"outcome": "conflict_to_owner"},  # a conflict recommends nothing
    {"source_field": " "},
    {"outcome": "mapped"},
    {"extra": 1},
])
def test_a_glossary_payload_recommends_by_its_outcome(bad):
    GlossaryProposalPayload(**_glossary_payload())
    GlossaryProposalPayload(**_glossary_payload(outcome="conflict_to_owner", recommended=None, options=[]))
    with pytest.raises(ValidationError):
        GlossaryProposalPayload(**_glossary_payload(**bad))


@pytest.mark.parametrize("bad", [
    {"candidates": []}, {"candidates": ["isin", "isin"], "recommended": None}, {"candidates": [f"k{n}" for n in range(11)]},
    {"recommended": None},  # one candidate is recommended
    {"candidates": ["isin", "cusip"]},  # a tie recommends nothing
    {"source_field": ""},
])
def test_a_mapping_payload_recommends_its_one_candidate_and_never_a_tie(bad):
    valid = {"source_field": "isin", "column_type": "VARCHAR", "candidates": ["isin"], "recommended": "isin",
             "reason": "r"}
    MappingProposalPayload(**valid)
    MappingProposalPayload(**{**valid, "candidates": ["isin", "cusip"], "recommended": None})
    with pytest.raises(ValidationError):
        MappingProposalPayload(**{**valid, **bad})
