"""Domain models validate what the database cannot say simply. Each task adds its models here."""

import pytest
from pydantic import ValidationError

from core.domain.glossary import DATA_TYPES, GlossaryElementDraft
from core.domain.proposal import PROPOSAL_KINDS, ProposalDraft

# ---- Proposal (task 4)


def test_proposal_kinds_are_the_seven_agents_with_an_approver():
    assert set(PROPOSAL_KINDS) == {
        "scope", "source_list", "profile_summary", "glossary_element", "mapping", "rule", "review_summary",
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
from core.domain.load import LOAD_STATUSES, LoadDraft  # noqa: E402
from core.domain.product import ProductDraft  # noqa: E402
from core.domain.requirement import REQUIREMENT_STATUSES, RequirementDraft  # noqa: E402


def test_status_lists_are_the_names_in_claude_md():
    assert REQUIREMENT_STATUSES == ("defined", "onboarding", "ready", "access_restricted", "rejected")
    assert FEED_STAGES == ("candidate", "registered", "rejected")
    assert LOAD_STATUSES == ("received", "held", "failed_rolled_back", "released")


@pytest.mark.parametrize(
    ("model", "valid", "bad"),
    [
        (RequirementDraft, {"requester_id": "dev-requester", "title": "T"}, {"title": " "}),
        (FeedDraft, {"code": "feed_a", "name": "Feed A"}, {"code": "Feed-A"}),
        (FeedDraft, {"code": "feed_a", "name": "Feed A"}, {"owner_id": " "}),
        (MappingDraft, {"source_field": "ClosePx", "element_key": "close_price"}, {"element_key": "Close"}),
        (MappingDraft, {"source_field": "ClosePx", "element_key": "close_price"}, {"source_field": ""}),
        (RuleDraft, {"rule_key": "r", "sql": "SELECT 1", "description": "D"}, {"sql": " "}),
        (LoadDraft, {"file_ref": "local://raw/x.csv"}, {"file_ref": ""}),
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
