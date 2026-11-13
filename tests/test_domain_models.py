"""Domain models validate what the database cannot say simply. Each task adds its models here."""

import pytest
from pydantic import ValidationError

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
