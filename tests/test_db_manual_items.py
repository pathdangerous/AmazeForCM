"""Migration 0028 (phase 7.12, T7): a proposal is made by an agent (a logged call, no author) or by a person (an author,
no logged call, never a model call). The database refuses anything in between, and a person's mapping may name any
glossary element that exists, where an agent's must be one of its candidates."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from core.repositories.configuration import MappingRepository
from core.repositories.errors import ProposalLogEntryNotFound
from core.repositories.proposals import ProposalRepository
from tests.configured import (
    a_registered_feed,
    approved_element,
    logged_call,
    mapping_proposal,
    new_element,
)
from tests.conftest import dev_user_for
from tests.test_db_onboarding_guards import refused

INSERT = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id, origin, "
          "author) VALUES (:log, :kind, CAST(:payload AS jsonb), 'A1', :roles, :feed, :origin, :author)")
RULE = {"rule_key": "px_present", "sql": "SELECT _row FROM curated WHERE px IS NULL", "description": "Price is present.",
        "element_keys": ["px"], "template": None, "backtest": {"status": "not_back_tested"}}


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def feed(s):
    return a_registered_feed(s, "syn_manual")


def params(feed, *, log=None, kind="rule", payload=None, origin="manual", author="dev-operator", roles=None):
    return {"log": log, "kind": kind, "payload": json.dumps(payload or RULE), "roles": roles or ["data_steward", "checker"],
            "feed": feed, "origin": origin, "author": author}


def test_a_manual_proposal_has_an_author_and_no_logged_call(s, feed):
    s.execute(text(INSERT), params(feed))
    row = s.execute(text("SELECT origin, author, proposal_log_id FROM proposals WHERE feed_id = :f AND kind = 'rule'"),
                    {"f": feed}).one()
    assert (row.origin, row.author, row.proposal_log_id) == ("manual", "dev-operator", None)


def test_a_manual_proposal_with_no_author_is_refused(s, feed):
    refused(s, "origin_matches_source", INSERT, **params(feed, author=None))
    refused(s, "origin_matches_source", INSERT, **params(feed, author="   "))


def test_a_manual_proposal_with_a_logged_call_is_refused(s, feed):
    log = logged_call(s, "RuleRecommenderAgent", "rule_recommender").id
    refused(s, "origin_matches_source", INSERT, **params(feed, log=log))


def test_an_agent_proposal_needs_its_logged_call_and_has_no_author(s, feed):
    refused(s, "origin_matches_source", INSERT, **params(feed, origin="agent", author=None))  # no call
    log = logged_call(s, "RuleRecommenderAgent", "rule_recommender").id
    refused(s, "origin_matches_source", INSERT, **params(feed, origin="agent", log=log))  # an author as well


def test_a_person_makes_only_glossary_mapping_transformation_and_rule_items(s, feed):
    refused(s, "manual_kinds", INSERT, **params(feed, kind="review_summary", payload={"summary": "x"},
                                                roles=["data_owner"]))
    refused(s, "manual_kinds", INSERT, **params(feed, kind="profile_summary", payload={"summary": "x"},
                                                roles=["backend_team"]))


def test_a_manual_item_names_its_feed(s, feed):
    refused(s, "manual_kinds", INSERT, **params(None))


def test_a_manual_rule_carries_its_back_test(s, feed):
    refused(s, "manual_rule_back_tested", INSERT, **params(feed, payload={k: v for k, v in RULE.items() if k != "backtest"}))


def test_the_repository_refuses_a_missing_call_for_an_agent_proposal(s, feed):
    with pytest.raises(TypeError):
        ProposalRepository(s).create(kind="rule", payload=RULE, autonomy="A2", required_approver_roles=["data_steward"])
    with pytest.raises(ProposalLogEntryNotFound):
        ProposalRepository(s).create(proposal_log_id="00000000-0000-0000-0000-000000000000", kind="rule", payload=RULE,
                                     autonomy="A2", required_approver_roles=["data_steward"], feed_id=feed)


def test_a_manual_proposal_comes_back_as_manual(s, feed):
    made = ProposalRepository(s).create_manual(kind="rule", payload=RULE, required_approver_roles=["data_steward", "checker"],
                                               feed_id=feed, author="dev-operator")
    assert (made.origin, made.author, made.proposal_log_id, made.autonomy, made.status) == (
        "manual", "dev-operator", None, "A1", "pending")


# ---------------------------------------------------------------- a mapping to any element that exists


def manual_mapping(s, feed, column, element, *, candidates=None):
    return ProposalRepository(s).create_manual(
        kind="mapping", feed_id=feed, author="dev-operator", required_approver_roles=["data_steward"],
        payload={"source_field": column, "column_type": None, "candidates": candidates or [element],
                 "recommended": element, "reason": "Chosen by hand."})


def approved(s, proposal):
    ProposalRepository(s).record_decision(proposal.id, user=dev_user_for("data_steward"), decision="approved")


def test_a_manual_mapping_may_name_an_element_the_agent_never_offered(s, feed):
    approved_element(s, new_element("some_element"), feed_id=feed, column="x")
    proposal = manual_mapping(s, feed, "px", "some_element")
    approved(s, proposal)
    row = MappingRepository(s).add_version(feed_id=feed, source_field="px", element_key="some_element",
                                           proposal_id=proposal.id)
    assert row.element_key == "some_element"


def test_a_manual_mapping_to_an_element_that_does_not_exist_is_refused(s, feed):
    proposal = manual_mapping(s, feed, "px", "no_such_element")
    approved(s, proposal)
    with pytest.raises(Exception, match="no_such_element"):
        MappingRepository(s).add_version(feed_id=feed, source_field="px", element_key="no_such_element",
                                         proposal_id=proposal.id)


def test_a_manual_mapping_row_must_be_the_proposals_recommended_element(s, feed):
    approved_element(s, new_element("elem_a"), feed_id=feed, column="a")
    approved_element(s, new_element("elem_b"), feed_id=feed, column="b")
    proposal = manual_mapping(s, feed, "px", "elem_a")
    approved(s, proposal)
    with pytest.raises(Exception, match="elem_b"):
        MappingRepository(s).add_version(feed_id=feed, source_field="px", element_key="elem_b", proposal_id=proposal.id)


def test_an_agents_mapping_still_needs_one_of_its_candidates(s, feed):
    approved_element(s, new_element("elem_c"), feed_id=feed, column="c")
    proposal = mapping_proposal(s, feed, "px", ["isin"], decision="approved")
    with pytest.raises(Exception, match="not a candidate"):
        MappingRepository(s).add_version(feed_id=feed, source_field="px", element_key="elem_c", proposal_id=proposal.id)



# ---------------------------------------------------------------- a go-live note: an edit of the summary's notes only


def test_a_review_summarys_notes_can_be_edited_and_nothing_else(s, feed):
    from core.repositories.errors import ProposalEditRefused

    repo = ProposalRepository(s)
    old = repo.create(proposal_log_id=logged_call(s, "ReviewSummariserAgent", "review_summariser").id,
                      kind="review_summary", payload={"summary": "Fine.", "notes": []}, autonomy="A1",
                      required_approver_roles=["data_owner"], feed_id=feed)
    owner = dev_user_for("data_owner")
    noted = repo.edit(old.id, payload={**old.payload, "notes": [{"text": "Seen by the desk."}]}, user=owner)
    assert (noted.origin, noted.proposal_log_id) == ("agent", old.proposal_log_id) and noted.status == "pending"
    with pytest.raises(ProposalEditRefused, match="only its notes"):
        repo.edit(noted.id, payload={**noted.payload, "summary": "Changed."}, user=owner)
    with pytest.raises(ProposalEditRefused, match="only its notes"):
        repo.edit(noted.id, payload={**noted.payload, "notes": "not a list"}, user=owner)


def test_an_edit_of_a_manual_item_stays_manual_and_cannot_change_its_author(s, feed):
    repo = ProposalRepository(s)
    old = repo.create_manual(kind="rule", payload=RULE, required_approver_roles=["data_steward", "checker"],
                             feed_id=feed, author="dev-operator")
    new = repo.edit(old.id, payload={**RULE, "description": "Changed."}, user=dev_user_for("data_steward"))
    assert (new.origin, new.author, new.proposal_log_id, new.edited_by) == ("manual", "dev-operator", None, "dev-data-steward")
    refused(s, "keeps the origin and author",
            "INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id, origin, "
            "author, edited_from, edited_by) VALUES (NULL, 'rule', CAST(:p AS jsonb), 'A1', :roles, :feed, 'manual', "
            "'someone-else', :old, 'x')",
            p=json.dumps({**RULE, "description": "Again."}), roles=["data_steward", "checker"], feed=feed, old=new.id)
