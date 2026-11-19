"""Editing a proposal before it is approved (phase 7.8 plan, assumption 36; migration 0024).

An edit is a NEW proposal row (same logged call, `edited_from`, `edited_by`) and the old row becomes `superseded`,
both in one statement. These tests prove the edit and prove that every guard still holds: the payload of a proposal
is still immutable, a superseded proposal can never be decided, one pending proposal per column, and a mapping or
glossary element row must still be one of the EDITED proposal's candidates or options.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from core.domain.mapping import ElementOption
from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.errors import (
    NotAnApprover,
    ProposalAlreadyDecided,
    ProposalEditRefused,
    ProposalForAnotherField,
    ProposalNotFound,
    WrongProposalKind,
)
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.requirements import RequirementRepository
from tests.configured import (
    GLENN,
    a_registered_feed,
    glossary_proposal,
    logged_call,
    mapping_proposal,
    new_element,
    rule_proposal,
    transformation_proposal,
)
from tests.conftest import dev_user_for
from tests.test_db_onboarding_guards import _branch_off, answered_by, refused


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def feed(s):
    return a_registered_feed(s, "syn_edit")


@pytest.fixture
def steward():
    return dev_user_for("data_steward")


@pytest.fixture
def repo(s):
    return ProposalRepository(s)


def a_tie(s, feed, column="px", **kw):
    return mapping_proposal(s, feed, column, ["isin", "cusip"], **kw)


def decided(s, proposal, *roles):
    for role in roles:
        ProposalRepository(s).record_decision(proposal.id, user=dev_user_for(role), decision="approved")
    return ProposalRepository(s).get(proposal.id)


EDIT = "SELECT amaze_edit_proposal(:old, CAST(:payload AS jsonb), :by)"
EDITED = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, requirement_id, "
          "feed_id, edited_from, edited_by, status) VALUES (:log, :kind, CAST(:payload AS jsonb), :auto, :roles, :req, "
          ":feed, :old, :by, :status)")


def edited_params(of, **over):
    return {"log": of.proposal_log_id, "kind": of.kind, "payload": json.dumps({**of.payload, "reason": "edited"}),
            "auto": of.autonomy, "roles": list(of.required_approver_roles), "req": of.requirement_id,
            "feed": of.feed_id, "old": of.id, "by": "dev-data-steward", "status": "pending", **over}


def edit_params(old, payload=None, by="dev-data-steward"):
    return {"old": old.id, "payload": json.dumps(payload if payload is not None else {**old.payload, "reason": "edited"}),
            "by": by}


EDIT_GUARD = "amaze_guard_proposal_edit"


# ---------------------------------------------------------------- the edit itself

def test_an_edit_is_a_new_pending_proposal_and_the_old_one_is_superseded(s, repo, feed, steward):
    old = a_tie(s, feed)
    new = repo.edit(old.id, payload={**old.payload, "recommended": "isin", "reason": "The steward chose."}, user=steward)
    assert new.id != old.id
    assert (new.status, new.edited_from, new.edited_by) == ("pending", old.id, steward.id)
    assert (new.proposal_log_id, new.kind, new.feed_id, new.requirement_id, new.autonomy,
            new.required_approver_roles) == (old.proposal_log_id, old.kind, old.feed_id, old.requirement_id,
                                             old.autonomy, old.required_approver_roles)
    assert new.payload["recommended"] == "isin" and new.decided_at is None
    after = repo.get(old.id)
    assert after.status == "superseded" and after.decided_at is not None
    assert after.payload == old.payload and (after.edited_from, after.edited_by) == (None, None)  # what was said stays


def test_the_logged_model_call_is_untouched(s, repo, feed, steward):
    old = a_tie(s, feed)
    before = s.execute(text("SELECT to_jsonb(l) FROM proposal_log l WHERE id = :id"), {"id": old.proposal_log_id}).scalar_one()
    count = s.scalar(text("SELECT count(*) FROM proposal_log"))
    new = repo.edit(old.id, payload={**old.payload, "recommended": "cusip"}, user=steward)
    assert s.scalar(text("SELECT count(*) FROM proposal_log")) == count
    assert s.execute(text("SELECT to_jsonb(l) FROM proposal_log l WHERE id = :id"),
                     {"id": old.proposal_log_id}).scalar_one() == before
    audit = repo.audit(new.id)
    assert audit["proposal_log_id"] == old.proposal_log_id and audit["agent_name"] == "MappingAgent"


def test_an_edit_can_be_edited_again_and_the_chain_reads_back(s, repo, feed, steward):
    first = a_tie(s, feed)
    second = repo.edit(first.id, payload={**first.payload, "recommended": "isin"}, user=steward)
    third = repo.edit(second.id, payload={**first.payload, "recommended": "cusip"}, user=steward)
    ids = [first.id, second.id, third.id]
    for start in ids:
        assert [p.id for p in repo.lineage(start)] == ids
    assert [p.status for p in repo.lineage(third.id)] == ["superseded", "superseded", "pending"]
    assert [p.edited_from for p in repo.lineage(first.id)] == [None, first.id, second.id]
    assert repo.pending_for_column("mapping", feed, "px").id == third.id


def test_a_proposal_that_was_never_edited_is_its_own_lineage(s, repo, feed):
    only = a_tie(s, feed)
    assert [p.id for p in repo.lineage(only.id)] == [only.id]


def test_the_lineage_of_a_missing_proposal(repo):
    with pytest.raises(ProposalNotFound):
        repo.lineage(uuid.UUID(int=1))


def test_a_superseded_proposal_cannot_be_edited_again(s, repo, feed, steward):
    old = a_tie(s, feed)
    repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    with pytest.raises(ProposalAlreadyDecided, match="superseded"):
        repo.edit(old.id, payload={**old.payload, "recommended": "cusip"}, user=steward)


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_a_decided_proposal_cannot_be_edited(s, repo, feed, steward, decision):
    old = a_tie(s, feed, decision=decision)
    with pytest.raises(ProposalAlreadyDecided):
        repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    answered_by(s, "only a pending proposal can be edited", EDIT, edit_params(old),
                _branch_off(EDIT_GUARD, "proposals: proposal % is %, and only a pending proposal can be edited"))


def test_an_edit_of_a_missing_proposal(repo, steward):
    with pytest.raises(ProposalNotFound):
        repo.edit(uuid.UUID(int=1), payload={"x": 1}, user=steward)


def test_only_a_person_who_could_decide_it_may_edit(s, repo, feed):
    old = a_tie(s, feed)
    with pytest.raises(NotAnApprover):
        repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=dev_user_for("requester"))
    assert repo.get(old.id).status == "pending"


@pytest.mark.parametrize("kind", ["profile_summary", "review_summary", "scope"])
def test_only_four_kinds_can_be_edited(s, repo, make_proposal, steward, kind):
    old = make_proposal(kind=kind, roles=("data_steward",))
    with pytest.raises(WrongProposalKind):
        repo.edit(old.id, payload={"note": "changed"}, user=steward)
    answered_by(s, "cannot be edited", EDIT, edit_params(old, {"note": "changed"}),
                _branch_off(EDIT_GUARD, "proposals: a % proposal cannot be edited"))


def test_an_edit_that_changes_nothing_is_refused(s, repo, feed, steward):
    old = a_tie(s, feed)
    with pytest.raises(ProposalEditRefused, match="changes nothing"):
        repo.edit(old.id, payload=dict(old.payload), user=steward)
    answered_by(s, "changes nothing", EDIT, edit_params(old, dict(old.payload)),
                _branch_off(EDIT_GUARD, "proposals: the edit of proposal % changes nothing"))
    assert repo.get(old.id).status == "pending"


def test_a_refused_edit_leaves_the_session_usable_and_the_old_proposal_pending(s, repo, feed, steward):
    old = a_tie(s, feed)
    with pytest.raises(ProposalEditRefused):
        repo.edit(old.id, payload={**old.payload, "candidates": ["isin", "cusip", "figi"]}, user=steward)
    assert repo.get(old.id).status == "pending" and repo.pending_for_column("mapping", feed, "px").id == old.id
    assert repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward).status == "pending"


# ---------------------------------------------------------------- a superseded proposal can never be decided

def test_a_superseded_proposal_cannot_be_approved_through_the_repository(s, repo, feed, steward):
    old = a_tie(s, feed)
    repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    for decision in ("approved", "rejected"):
        with pytest.raises(ProposalAlreadyDecided, match="superseded"):
            repo.record_decision(old.id, user=steward, decision=decision)
    with pytest.raises(ProposalAlreadyDecided):
        repo.approve_my_steps(old.id, user=steward)


APPROVE = ("INSERT INTO approvals (proposal_id, step, role, decided_by, decision) "
           "VALUES (:p, 1, 'data_steward', 'dev-data-steward', :decision)")


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_a_superseded_proposal_takes_no_row_in_approvals(s, repo, feed, steward, decision):
    old = a_tie(s, feed)
    repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    answered_by(s, "is superseded", APPROVE, {"p": old.id, "decision": decision},
                "DROP TRIGGER trg_approvals_not_superseded ON approvals")


def test_the_edited_proposal_takes_the_decision_instead(s, repo, feed, steward):
    old = a_tie(s, feed)
    new = repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    s.execute(text(APPROVE), {"p": new.id, "decision": "approved"})


@pytest.mark.parametrize("status", ["pending", "approved", "rejected"])
def test_a_superseded_proposal_never_changes_status_again(s, repo, feed, steward, status):
    old = a_tie(s, feed)
    repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    sql = "UPDATE proposals SET status = :status, decided_at = now() WHERE id = :id"
    answered_by(s, "a decided proposal never changes again", sql, {"status": status, "id": old.id},
                _branch_off("amaze_guard_proposal_change",
                            "proposals: proposal % is %, and a decided proposal never changes again"))


def test_a_configuration_row_cannot_come_from_a_superseded_proposal(s, repo, feed, steward):
    old = a_tie(s, feed)
    repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    answered_by(s, "is superseded, not approved", MAP, {"feed": feed, "field": "px", "key": "isin", "version": 1,
                                                       "p": old.id},
                _branch_off("amaze_require_approved_proposal", "proposal % is %, not approved"))


# ---------------------------------------------------------------- the payload of a proposal stays immutable

@pytest.mark.parametrize("change", ["payload = '{\"source_field\": \"px\", \"candidates\": [\"figi\"]}'::jsonb",
                                    "edited_by = 'someone-else'", "edited_from = id", "kind = 'rule'",
                                    "proposal_log_id = gen_random_uuid()"])
@pytest.mark.parametrize("which", ["original", "edited"])
def test_an_update_of_anything_but_the_decision_is_still_refused(s, repo, feed, steward, change, which):
    old = a_tie(s, feed)
    new = repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    target = old if which == "original" else new
    answered_by(s, "is otherwise fixed", f"UPDATE proposals SET {change} WHERE id = :id", {"id": target.id},
                _branch_off("amaze_guard_proposal_change",
                            "proposals: only status and decided_at change (by a decision); proposal % is otherwise fixed"))


def test_a_proposal_is_still_never_deleted(s, repo, feed, steward):
    old = a_tie(s, feed)
    repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    answered_by(s, "DELETE refused", "DELETE FROM proposals WHERE id = :id", {"id": old.id},
                _branch_off("amaze_guard_proposal_change", "proposals: DELETE refused; a proposal is decided, never removed"))


# ---------------------------------------------------------------- a superseded status needs an edit (deferred check)

def test_a_proposal_superseded_with_no_edit_fails_at_commit(s, feed):
    old = a_tie(s, feed)
    s.execute(text("UPDATE proposals SET status = 'superseded', decided_at = now() WHERE id = :id"), {"id": old.id})
    answered_by(s, "superseded with no edit", "SET CONSTRAINTS ALL IMMEDIATE", {},
                _branch_off("amaze_require_edit_of_superseded", "proposals: proposal % was superseded with no edit"))


def test_an_edit_satisfies_the_commit_check(s, repo, feed, steward):
    old = a_tie(s, feed)
    repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def test_two_edits_of_one_proposal_never_exist(s, repo, feed, steward):
    """A proposal has one edit: the second is refused (its old row is superseded; uq_proposals_edited_from is the
    backstop)."""
    old = a_tie(s, feed)
    repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    assert s.scalar(text("SELECT count(*) FROM proposals WHERE edited_from = :id"), {"id": old.id}) == 1
    refused(s, "only a pending proposal can be edited", EDITED,
            **edited_params(repo.get(old.id), payload=json.dumps({**old.payload, "reason": "another"})))


# ---------------------------------------------------------------- one pending proposal per column

def test_one_pending_proposal_per_column_holds_after_edits(s, repo, feed, steward):
    old = a_tie(s, feed)
    other_column = a_tie(s, feed, "qty")
    new = repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    pending = repo.list_for_feed_column("mapping", feed, "px", status="pending")
    assert [p.id for p in pending] == [new.id]
    assert repo.pending_for_column("mapping", feed, "qty").id == other_column.id
    copy = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
            "SELECT proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id FROM proposals "
            "WHERE id = :id")
    answered_by(s, "uq_proposals_one_pending_per_feed_column", copy, {"id": new.id},
                "DROP INDEX uq_proposals_one_pending_per_feed_column")


# ---------------------------------------------------------------- a mapping is still one of its candidates

MAP = ("INSERT INTO mappings (feed_id, source_field, element_key, version, proposal_id) "
       "VALUES (:feed, :field, :key, :version, :p)")


def test_a_mapping_edit_picks_another_candidate_and_the_row_is_written(s, repo, feed, steward):
    old = a_tie(s, feed)
    new = repo.edit(old.id, payload={**old.payload, "recommended": "cusip"}, user=steward)
    repo.record_decision(new.id, user=steward, decision="approved")
    mapping = MappingRepository(s).add_version(feed_id=feed, source_field="px", element_key="cusip", proposal_id=new.id)
    assert (mapping.element_key, mapping.proposal_id) == ("cusip", new.id)


def test_a_mapping_edit_to_a_non_candidate_is_refused(s, repo, feed, steward):
    old = a_tie(s, feed)
    with pytest.raises(ProposalEditRefused, match="not a candidate"):
        repo.edit(old.id, payload={**old.payload, "recommended": "figi"}, user=steward)
    answered_by(s, "not a candidate", EDIT, edit_params(old, {**old.payload, "recommended": "figi"}),
                _branch_off(EDIT_GUARD, "proposals: an edit of mapping proposal % recommends %, which is not a candidate"))


@pytest.mark.parametrize("candidates", [["isin", "cusip", "figi"], ["isin"], ["cusip", "isin"], []])
def test_a_mapping_edit_cannot_change_the_candidates(s, repo, feed, steward, candidates):
    old = a_tie(s, feed)
    with pytest.raises(ProposalEditRefused, match="candidates"):
        repo.edit(old.id, payload={**old.payload, "candidates": candidates}, user=steward)
    answered_by(s, "keeps its candidates", EDIT, edit_params(old, {**old.payload, "candidates": candidates}),
                _branch_off(EDIT_GUARD, "proposals: an edit of mapping proposal % keeps its candidates"))


def test_the_edited_mapping_proposal_still_refuses_a_row_that_is_not_a_candidate(s, repo, feed, steward):
    old = a_tie(s, feed)
    new = repo.edit(old.id, payload={**old.payload, "recommended": "isin"}, user=steward)
    repo.record_decision(new.id, user=steward, decision="approved")
    answered_by(s, "is not a candidate", MAP, {"feed": feed, "field": "px", "key": "figi", "version": 1, "p": new.id},
                _branch_off("amaze_require_mapping_matches_proposal", "mapping: % is not a candidate of proposal %"))


# ---------------------------------------------------------------- a glossary element is still one of the edited options

ROW = ("INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, aliases, created_by, "
       "proposal_id) VALUES (:key, :version, :name, :domain, :data_type, :definition, :aliases, :by, :p)")
NONE_OF = _branch_off("amaze_require_glossary_option", "glossary element % version % is none of the options of proposal %")


def row_of(option, proposal, **over):
    element = option.element
    return {"key": element.key, "version": 1, "name": element.name, "domain": element.domain,
            "data_type": element.data_type, "definition": element.definition, "aliases": list(element.aliases),
            "by": GLENN, "p": proposal.id, **over}


def test_a_glossary_edit_changes_the_definition_and_the_element_must_equal_the_edited_option(s, repo, feed):
    original = new_element("syn_edit_element", definition="As first proposed.")
    old = glossary_proposal(s, [original], feed_id=feed, column="px_g")
    edited = new_element("syn_edit_element", definition="As edited by the owner.", name="Edited name",
                         data_type="decimal")
    owner = dev_user_for("glossary_owner")
    new = repo.edit(old.id, payload={**old.payload, "options": [edited.model_dump(mode="json")]}, user=owner)
    repo.record_decision(new.id, user=owner, decision="approved")
    # the original option is no longer offered: its row is refused ...
    answered_by(s, "is none of the options", ROW, row_of(original, new), NONE_OF)
    # ... and a row that equals the edited option is accepted
    glossary = GlossaryRepository(s)
    glossary.lock()
    written = glossary.add_version(proposal_id=new.id, created_by=GLENN, option=edited)
    assert (written.definition, written.name, written.data_type) == ("As edited by the owner.", "Edited name", "decimal")


def test_a_glossary_edit_may_set_the_unit_and_the_element_must_equal_the_edited_option(s, repo, feed):
    """Task 6.1: the unit of a new element is editable like its name and definition (0024's guard fixes only the
    number, kind, key and alias of the options); the 0025 element guard then compares the unit with the edited one."""
    original = new_element("syn_edit_unit", data_type="decimal")
    old = glossary_proposal(s, [original], feed_id=feed, column="px_u")
    edited = ElementOption(kind="new_element", key="syn_edit_unit",
                           element=original.element.model_copy(update={"unit": "percent"}))
    owner = dev_user_for("glossary_owner")
    new = repo.edit(old.id, payload={**old.payload, "options": [edited.model_dump(mode="json")]}, user=owner)
    repo.record_decision(new.id, user=owner, decision="approved")
    insert = ("INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, aliases, unit, "
              "created_by, proposal_id) VALUES (:key, :version, :name, :domain, :data_type, :definition, :aliases, "
              ":unit, :by, :p)")
    answered_by(s, "is none of the options", insert, {**row_of(original, new), "unit": None}, NONE_OF)  # no unit: refused
    glossary = GlossaryRepository(s)
    glossary.lock()
    written = glossary.add_version(proposal_id=new.id, created_by=GLENN, option=edited)
    assert written.unit == "percent"


def test_a_glossary_row_cannot_come_from_the_superseded_proposal(s, repo, feed):
    original = new_element("syn_edit_element_b")
    old = glossary_proposal(s, [original], feed_id=feed, column="px_b")
    owner = dev_user_for("glossary_owner")
    repo.edit(old.id, payload={**old.payload, "reason": "edited"}, user=owner)
    answered_by(s, "is superseded, not approved", ROW, row_of(original, old),
                _branch_off("amaze_require_approved_proposal", "proposal % is %, not approved"))


KEEPS_KEY = "proposals: an edit of glossary proposal % keeps the kind and key of each option"
KEEPS_NUMBER = "proposals: an edit of glossary proposal % keeps the number of options"


@pytest.mark.parametrize(("options", "message"), [
    ([], KEEPS_NUMBER),                                                                    # an option less
    ([new_element("syn_edit_element_c").model_dump(mode="json"),
      new_element("syn_edit_element_d").model_dump(mode="json")], KEEPS_NUMBER),           # an option more
    ([new_element("syn_edit_other_key").model_dump(mode="json")], KEEPS_KEY),              # another key
    ([{"kind": "alias", "key": "syn_edit_element_c", "alias": "syn_alias", "element": None}], KEEPS_KEY),  # another kind
])
def test_a_glossary_edit_keeps_the_number_kind_and_key_of_the_options(s, repo, feed, options, message):
    old = glossary_proposal(s, [new_element("syn_edit_element_c")], feed_id=feed, column="px_c")
    owner = dev_user_for("glossary_owner")
    with pytest.raises(ProposalEditRefused, match="option"):
        repo.edit(old.id, payload={**old.payload, "options": options}, user=owner)
    answered_by(s, "number of options" if message == KEEPS_NUMBER else "kind and key of each option", EDIT,
                edit_params(old, {**old.payload, "options": options}, by="dev-glossary-owner"),
                _branch_off(EDIT_GUARD, message))


def test_a_glossary_alias_edit_keeps_its_alias(s, repo, feed):
    from tests.configured import alias

    old = glossary_proposal(s, [alias("isin", "syn_isin_alias")], feed_id=feed, column="px_alias")
    owner = dev_user_for("glossary_owner")
    other = alias("isin", "syn_isin_other").model_dump(mode="json")
    with pytest.raises(ProposalEditRefused, match="option"):
        repo.edit(old.id, payload={**old.payload, "options": [other]}, user=owner)


# ---------------------------------------------------------------- rules and transformations keep their identity

def test_a_rule_edit_changes_the_sql_and_the_row_must_hold_the_edited_sql(s, repo, feed, steward):
    old = rule_proposal(s, feed, "close_price_present")
    new_sql = "SELECT _row FROM curated WHERE close_price <= 0"
    new = repo.edit(old.id, payload={**old.payload, "sql": new_sql}, user=steward)
    repo.record_decision(new.id, user=steward, decision="approved")
    repo.record_decision(new.id, user=dev_user_for("checker"), decision="approved")
    rules = RuleRepository(s)
    with pytest.raises(ProposalForAnotherField):
        rules.add_version(feed_id=feed, rule_key="close_price_present", sql=old.payload["sql"],
                          description=old.payload["description"], proposal_id=new.id)
    rule = rules.add_version(feed_id=feed, rule_key="close_price_present", sql=new_sql,
                             description=old.payload["description"], proposal_id=new.id)
    assert (rule.sql, rule.proposal_id) == (new_sql, new.id)


def test_a_rule_edit_keeps_its_rule_key(s, repo, feed, steward):
    old = rule_proposal(s, feed, "close_price_present")
    with pytest.raises(ProposalEditRefused, match="rule key"):
        repo.edit(old.id, payload={**old.payload, "rule_key": "another_rule"}, user=steward)
    answered_by(s, "keeps its rule key", EDIT, edit_params(old, {**old.payload, "rule_key": "another_rule"}),
                _branch_off(EDIT_GUARD, "proposals: an edit of proposal % keeps its rule key"))


def test_a_transformation_edit_changes_the_steps_but_keeps_the_column(s, repo, feed, steward):
    old = transformation_proposal(s, feed, "px", [{"op": "trim"}])
    new = repo.edit(old.id, payload={**old.payload, "steps": [{"op": "trim"}, {"op": "cast", "type": "string"}]},
                    user=steward)
    assert new.payload["steps"] == [{"op": "trim"}, {"op": "cast", "type": "string"}]
    with pytest.raises(ProposalEditRefused, match="column"):
        repo.edit(new.id, payload={**new.payload, "source_field": "qty"}, user=steward)
    answered_by(s, "keeps its column", EDIT, edit_params(new, {**new.payload, "source_field": "qty"}),
                _branch_off(EDIT_GUARD, "proposals: an edit of proposal % keeps its column"))


# ---------------------------------------------------------------- a direct INSERT is guarded the same way

def test_a_direct_insert_as_an_edit_is_accepted_when_it_is_one(s, repo, feed):
    old = a_tie(s, feed)
    s.execute(text(EDITED), edited_params(old))
    assert repo.get(old.id).status == "superseded"  # the insert moved the old one aside, in the same statement


def test_an_edit_keeps_the_logged_call(s, feed):
    old = a_tie(s, feed)
    other = logged_call(s, "MappingAgent", "mapping").id
    answered_by(s, "keeps the logged call", EDITED, edited_params(old, log=other),
                _branch_off(EDIT_GUARD, "proposals: an edit keeps the logged call of proposal %"))


def test_an_edit_keeps_the_kind(s, feed):
    old = a_tie(s, feed)
    payload = json.dumps({"source_field": "px", "steps": [{"op": "trim"}]})
    answered_by(s, "keeps the kind", EDITED, edited_params(old, kind="transformation", payload=payload),
                _branch_off(EDIT_GUARD, "proposals: an edit keeps the kind of proposal %"))


def test_an_edit_keeps_the_feed(s, feed):
    old = a_tie(s, feed)
    other_feed = a_registered_feed(s, "syn_edit_other")
    answered_by(s, "keeps the feed", EDITED, edited_params(old, feed=other_feed),
                _branch_off(EDIT_GUARD, "proposals: an edit keeps the feed of proposal %"))


def test_an_edit_keeps_the_requirement(s, feed):
    old = a_tie(s, feed)
    requirement = RequirementRepository(s).create(requester_id="dev-requester", title="Synthetic", element_keys=[], terms=[])
    answered_by(s, "keeps the requirement", EDITED, edited_params(old, req=requirement.id),
                _branch_off(EDIT_GUARD, "proposals: an edit keeps the requirement of proposal %"))
    # the other way round: a proposal made for a requirement keeps it
    with_req = ProposalRepository(s).create(proposal_log_id=old.proposal_log_id, kind="mapping",
                                            payload={**old.payload, "source_field": "qty"}, autonomy="A2",
                                            required_approver_roles=["data_steward"], feed_id=feed,
                                            requirement_id=requirement.id)
    answered_by(s, "keeps the requirement", EDITED, edited_params(with_req, req=None),
                _branch_off(EDIT_GUARD, "proposals: an edit keeps the requirement of proposal %"))


def test_an_edit_keeps_the_approvers(s, feed):
    old = a_tie(s, feed)
    answered_by(s, "keeps the autonomy and approver roles", EDITED,
                edited_params(old, roles=["data_steward", "checker"]),
                _branch_off(EDIT_GUARD, "proposals: an edit keeps the autonomy and approver roles of proposal %"))
    answered_by(s, "keeps the autonomy and approver roles", EDITED, edited_params(old, auto="A1"),
                _branch_off(EDIT_GUARD, "proposals: an edit keeps the autonomy and approver roles of proposal %"))


def test_an_edit_starts_pending(s, feed):
    old = a_tie(s, feed)
    answered_by(s, "starts pending", EDITED, edited_params(old, status="rejected"),
                _branch_off(EDIT_GUARD, "proposals: an edited proposal starts pending"))


def test_an_edit_of_a_decided_proposal_by_direct_insert(s, feed):
    old = a_tie(s, feed, decision="approved")
    answered_by(s, "only a pending proposal can be edited", EDITED, edited_params(old),
                _branch_off(EDIT_GUARD, "proposals: proposal % is %, and only a pending proposal can be edited"))


def test_an_edit_of_a_proposal_that_does_not_exist(s, feed):
    old = a_tie(s, feed)
    answered_by(s, "does not exist", EDITED, edited_params(old, old=uuid.UUID(int=1)),
                _branch_off(EDIT_GUARD, "proposals: proposal % to edit does not exist"))


@pytest.mark.parametrize("payload", ['[]', '"x"', 'null'])
def test_an_edit_payload_is_a_json_object(s, feed, payload):
    old = a_tie(s, feed)
    answered_by(s, "must be a JSON object", EDITED, edited_params(old, payload=payload),
                _branch_off(EDIT_GUARD, "proposals: the edit of proposal % must be a JSON object"))


def test_who_edited_it_is_named(s, feed):
    old = a_tie(s, feed)
    answered_by(s, "ck_proposals_edited_by", EDITED, edited_params(old, by="  "),
                "ALTER TABLE proposals DROP CONSTRAINT ck_proposals_edited_by")
    answered_by(s, "must be named", EDIT, edit_params(old, by=""),
                _branch_off("amaze_edit_proposal", "amaze_edit_proposal: who edited proposal % must be named"))


def test_edited_from_and_edited_by_come_together(s, feed):
    old = a_tie(s, feed)
    answered_by(s, "ck_proposals_edited_from_and_by_together", EDITED, edited_params(old, by=None),
                "ALTER TABLE proposals DROP CONSTRAINT ck_proposals_edited_from_and_by_together")
    # edited_by without edited_from: a pending proposal that claims an edit it is not
    plain = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id, "
             "edited_by) VALUES (:log, 'mapping', CAST(:payload AS jsonb), 'A2', ARRAY['data_steward'], :feed, 'x')")
    answered_by(s, "ck_proposals_edited_from_and_by_together", plain,
                {"log": old.proposal_log_id, "payload": json.dumps({"source_field": "qty", "candidates": ["isin"]}),
                 "feed": feed},
                "ALTER TABLE proposals DROP CONSTRAINT ck_proposals_edited_from_and_by_together")


def test_the_status_list_has_superseded_and_nothing_else_new(s, feed):
    old = a_tie(s, feed)
    sql = "UPDATE proposals SET status = :status, decided_at = now() WHERE id = :id"
    refused(s, "ck_proposals_status|moves only to approved or rejected", sql, status="withdrawn", id=old.id)
