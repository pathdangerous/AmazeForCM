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
