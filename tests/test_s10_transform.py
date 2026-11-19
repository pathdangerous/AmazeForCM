"""Step 10, transformations and the key policy (phase 7.8, task 4.3, assumptions 27 to 29).

Outcomes of the propose half: transforms_proposed, nothing_to_transform. Not outcomes: a stored transformation, a
rejection, a stored key policy."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from agents.transformation import TransformationAgent
from core.repositories.errors import (
    ConfigurationChangedMeanwhile,
    FeedNotRegistered,
    NotAnApprover,
    NothingToTransform,
    ProposalAlreadyDecided,
    TransformationAlreadyProposed,
    WrongProposalKind,
)
from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.settings import FourEyesRepository
from core.repositories.transformations import KeyPolicyRepository, TransformationRepository
from core.steps.errors import NoteNeeded
from core.steps.s10_transform import (
    NoTransformNeeded,
    TransformsProposed,
    UnknownKeyColumn,
    decide_transformation,
    effective_key_policy,
    propose_transformations,
    set_key_policy,
    transform_input,
)
from ports.identity.dev import DevIdentity
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.configured import (
    a_registered_feed,
    approved_element,
    approved_mapping,
    approved_transformation,
    mapping_proposal,
    new_element,
    transformation_proposal,
)
from tests.conftest import dev_user_for
from tests.step_chains import end_read
from tests.test_agent_transformation import master_mapped

PLACEHOLDER = [{"op": "placeholder_to_null", "values": ["ALL"]}]


@pytest.fixture
def master(db_session, glossary, tmp_path):
    return master_mapped(db_session, tmp_path)


@pytest.fixture
def agent(tmp_path):
    return TransformationAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "blob"))


def propose(db_session, agent, feed_id):
    agent_input = transform_input(db_session, feed_id=feed_id)
    end_read(db_session)
    return propose_transformations(db_session, agent=agent, agent_input=agent_input)


# ---------------------------------------------------------------- the input


def test_the_input_holds_the_mapped_columns_with_their_facts_and_element_types(db_session, master):
    agent_input = transform_input(db_session, feed_id=master)
    by_name = {c.source_field: c for c in agent_input.columns}
    assert sorted(by_name) == sorted(["TICKER_NAME", "COUNTRY", "REGION", "F_PERIOD", "RECORD_DATE", "BEST_EPS"])
    country, date = by_name["COUNTRY"], by_name["RECORD_DATE"]
    assert (country.element_key, country.element_type, country.duckdb_type) == ("sec_country", "string", "VARCHAR")
    assert (country.placeholder_values, country.placeholder_count, country.all_empty) == (["ALL"], 70, True)
    assert (date.element_type, date.text_date_format, date.date_format_ambiguous) == ("date", "DD-MM-YYYY", False)
    assert by_name["BEST_EPS"].element_type == "decimal"


def test_an_unmapped_column_is_not_in_the_input(db_session, master):
    columns = {c.source_field for c in transform_input(db_session, feed_id=master).columns}
    assert "SHORTNAME" not in columns and "SECTOR" not in columns  # in the profile, never mapped


def test_a_column_with_a_pending_proposal_or_a_current_transformation_is_left_out(db_session, master):
    transformation_proposal(db_session, master, "COUNTRY", PLACEHOLDER)
    approved_transformation(db_session, master, "REGION", PLACEHOLDER)
    columns = {c.source_field for c in transform_input(db_session, feed_id=master).columns}
    assert "COUNTRY" not in columns and "REGION" not in columns and "F_PERIOD" in columns


def test_a_feed_with_no_mapped_column_has_nothing_to_transform(db_session, glossary):
    feed_id = a_registered_feed(db_session)
    with pytest.raises(NothingToTransform):
        transform_input(db_session, feed_id=feed_id)


def test_every_mapped_column_taken_has_nothing_to_transform(db_session, master):
    for field in ("TICKER_NAME", "COUNTRY", "REGION", "F_PERIOD", "RECORD_DATE", "BEST_EPS"):
        transformation_proposal(db_session, master, field, [{"op": "trim"}])
    with pytest.raises(NothingToTransform):
        transform_input(db_session, feed_id=master)


def test_a_candidate_feed_is_refused(db_session):
    feed = FeedRepository(db_session).create_candidate(code="syn_candidate", name="Candidate")
    with pytest.raises(FeedNotRegistered):
        transform_input(db_session, feed_id=feed.id)


# ---------------------------------------------------------------- propose


def test_propose_makes_one_pending_proposal_per_column_with_steps(db_session, master, agent):
    result = propose(db_session, agent, master)
    assert isinstance(result, TransformsProposed) and result.outcome == "transforms_proposed"
    pending = ProposalRepository(db_session).list_for_feed(master, "transformation", "pending")
    assert sorted(p.id for p in pending) == sorted(result.proposal_ids)
    assert sorted(p.payload["source_field"] for p in pending) == ["COUNTRY", "F_PERIOD", "RECORD_DATE", "REGION"]
    assert len({p.proposal_log_id for p in pending}) == 1 and result.log_id == pending[0].proposal_log_id


def test_propose_on_clean_columns_is_nothing_to_transform_and_still_logs_the_call(db_session, glossary, agent):
    feed_id = a_registered_feed(db_session)
    approved_mapping(db_session, feed_id, "qty", "volume")  # the default profile's one column, a clean BIGINT
    result = propose(db_session, agent, feed_id)
    assert isinstance(result, NoTransformNeeded) and result.outcome == "nothing_to_transform"
    assert ProposalRepository(db_session).list_for_feed(feed_id, "transformation") == []
    assert db_session.execute(text("SELECT count(*) FROM proposal_log WHERE agent_name = 'TransformationAgent'")
                              ).scalar_one() == 1


def test_a_proposal_made_during_the_call_discards_the_answer(db_session, master, agent):
    agent_input = transform_input(db_session, feed_id=master)
    transformation_proposal(db_session, master, "COUNTRY", PLACEHOLDER)  # someone else got there first
    end_read(db_session)
    with pytest.raises(TransformationAlreadyProposed):
        propose_transformations(db_session, agent=agent, agent_input=agent_input)
    assert db_session.execute(text("SELECT count(*) FROM proposal_log_discards")).scalar_one() == 1
    assert len(ProposalRepository(db_session).list_for_feed(master, "transformation")) == 1  # only theirs


def test_a_mapping_changed_during_the_call_discards_the_answer(db_session, master, agent):
    agent_input = transform_input(db_session, feed_id=master)
    approved_element(db_session, new_element("sec_country_code"), feed_id=master, column="COUNTRY")
    approved_mapping(db_session, master, "COUNTRY", "sec_country_code")
    end_read(db_session)
    with pytest.raises(ConfigurationChangedMeanwhile):
        propose_transformations(db_session, agent=agent, agent_input=agent_input)
    assert db_session.execute(text("SELECT count(*) FROM proposal_log_discards")).scalar_one() == 1
    assert ProposalRepository(db_session).list_for_feed(master, "transformation") == []


def test_a_transformation_approved_during_the_call_discards_the_answer(db_session, master, agent):
    agent_input = transform_input(db_session, feed_id=master)
    approved_transformation(db_session, master, "COUNTRY", PLACEHOLDER)
    end_read(db_session)
    with pytest.raises(ConfigurationChangedMeanwhile):
        propose_transformations(db_session, agent=agent, agent_input=agent_input)
    assert db_session.execute(text("SELECT count(*) FROM proposal_log_discards")).scalar_one() == 1


# ---------------------------------------------------------------- the steward's decision


def proposal_for(db_session, master, agent, column="COUNTRY"):
    propose(db_session, agent, master)
    return ProposalRepository(db_session).pending_for_column("transformation", master, column)


def test_an_approval_stores_a_transformation_equal_to_the_proposal(db_session, master, agent):
    proposal = proposal_for(db_session, master, agent)
    steward = dev_user_for("data_steward")
    stored = decide_transformation(db_session, proposal_id=proposal.id, user=steward, decision="approved", note=None)
    row = stored.transformation
    assert (row.source_field, row.version, row.proposal_id, row.created_by, row.feed_id) == (
        "COUNTRY", 1, proposal.id, "dev-data-steward", master)
    assert [s.model_dump(mode="json") for s in row.steps] == proposal.payload["steps"] == PLACEHOLDER
    assert ProposalRepository(db_session).get(proposal.id).status == "approved"
    assert [t.id for t in TransformationRepository(db_session).current_for_feed(master)] == [row.id]


def test_a_new_proposal_after_an_approval_would_make_the_next_version(db_session, master, agent):
    first = proposal_for(db_session, master, agent)
    decide_transformation(db_session, proposal_id=first.id, user=dev_user_for("data_steward"), decision="approved",
                          note=None)
    second = transformation_proposal(db_session, master, "COUNTRY", [{"op": "trim"}])
    stored = decide_transformation(db_session, proposal_id=second.id, user=dev_user_for("data_steward"),
                                   decision="approved", note=None)
    assert stored.transformation.version == 2


def test_a_rejection_writes_only_the_decision(db_session, master, agent):
    proposal = proposal_for(db_session, master, agent)
    rejected = decide_transformation(db_session, proposal_id=proposal.id, user=dev_user_for("data_steward"),
                                     decision="rejected", note="Leave the placeholder as a value.")
    assert (rejected.proposal_id, rejected.source_field) == (proposal.id, "COUNTRY")
    assert ProposalRepository(db_session).get(proposal.id).status == "rejected"
    assert TransformationRepository(db_session).current_for_feed(master) == []


def test_a_rejection_needs_a_note(db_session, master, agent):
    proposal = proposal_for(db_session, master, agent)
    with pytest.raises(NoteNeeded):
        decide_transformation(db_session, proposal_id=proposal.id, user=dev_user_for("data_steward"),
                              decision="rejected", note=" ")


@pytest.mark.parametrize("role", ["backend_team", "data_owner", "checker", "glossary_owner"])
def test_only_a_data_steward_decides(db_session, master, agent, role):
    proposal = proposal_for(db_session, master, agent)
    with pytest.raises(NotAnApprover):
        decide_transformation(db_session, proposal_id=proposal.id, user=dev_user_for(role), decision="approved",
                              note=None)
    assert TransformationRepository(db_session).current_for_feed(master) == []


def test_a_decided_proposal_cannot_be_decided_again(db_session, master, agent):
    proposal = proposal_for(db_session, master, agent)
    steward = dev_user_for("data_steward")
    decide_transformation(db_session, proposal_id=proposal.id, user=steward, decision="approved", note=None)
    with pytest.raises(ProposalAlreadyDecided):
        decide_transformation(db_session, proposal_id=proposal.id, user=steward, decision="approved", note=None)


def test_another_kind_of_proposal_is_refused(db_session, master):
    mapping = mapping_proposal(db_session, master, "SHORTNAME", ["sec_ticker_name"])
    with pytest.raises(WrongProposalKind):
        decide_transformation(db_session, proposal_id=mapping.id, user=dev_user_for("data_steward"),
                              decision="approved", note=None)


def test_with_four_eyes_off_the_operator_approves(db_session, master, agent):
    FourEyesRepository(db_session).set(False, set_by="dev-operator", reason="synthetic test")
    operator = DevIdentity().get_user("dev-operator")
    proposal = proposal_for(db_session, master, agent)
    stored = decide_transformation(db_session, proposal_id=proposal.id, user=operator, decision="approved", note=None)
    assert stored.transformation.created_by == "dev-operator"
    assert [a.decided_by for a in ProposalRepository(db_session).approvals(proposal.id)] == ["dev-operator"]


# ---------------------------------------------------------------- the key policy


def test_the_default_key_policy_is_flag_on_the_profile_summarys_key(db_session, master):
    effective = effective_key_policy(db_session, master)
    assert (effective.policy, effective.source, effective.version) == ("flag", "default", None)
    assert effective.key_columns == []  # the test chain's approved summary names no key


def test_setting_the_key_policy_stores_the_next_version_and_it_becomes_the_effective_one(db_session, master):
    steward = dev_user_for("data_steward")
    first = set_key_policy(db_session, feed_id=master, user=steward, policy="hold", key_columns=["TICKER_NAME"],
                           reason="Duplicates must stop the load.")
    second = set_key_policy(db_session, feed_id=master, user=steward, policy="keep_last",
                            key_columns=["TICKER_NAME", "SECTOR"], reason="The last row is right.")
    assert (first.key_policy.version, second.key_policy.version) == (1, 2)
    effective = effective_key_policy(db_session, master)
    assert (effective.policy, effective.key_columns, effective.source, effective.version, effective.set_by) == (
        "keep_last", ["TICKER_NAME", "SECTOR"], "set", 2, "dev-data-steward")
    assert effective.reason == "The last row is right."
    assert KeyPolicyRepository(db_session).current(master).version == 2


def test_the_key_policy_names_only_columns_of_the_latest_profile(db_session, master):
    with pytest.raises(UnknownKeyColumn, match="NO_SUCH"):
        set_key_policy(db_session, feed_id=master, user=dev_user_for("data_steward"), policy="flag",
                       key_columns=["TICKER_NAME", "NO_SUCH"], reason="Typo.")
    assert KeyPolicyRepository(db_session).current(master) is None


def test_only_a_data_steward_sets_the_key_policy(db_session, master):
    with pytest.raises(NotAnApprover):
        set_key_policy(db_session, feed_id=master, user=dev_user_for("data_owner"), policy="hold",
                       key_columns=["TICKER_NAME"], reason="Not mine to set.")
    assert KeyPolicyRepository(db_session).current(master) is None


def test_a_candidate_feed_has_no_key_policy_to_set(db_session):
    feed = FeedRepository(db_session).create_candidate(code="syn_candidate", name="Candidate")
    with pytest.raises(FeedNotRegistered):
        set_key_policy(db_session, feed_id=feed.id, user=dev_user_for("data_steward"), policy="hold",
                       key_columns=["a"], reason="Early.")
