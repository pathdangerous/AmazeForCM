"""Phase 7.8, task 4.1: TransformationRepository and KeyPolicyRepository (plan assumptions 28 and 29)."""

from __future__ import annotations

import pytest

from core.repositories.errors import (
    FeedNotFound,
    FeedNotRegistered,
    ProposalForAnotherField,
    ProposalNotApproved,
    SourceFieldNotMapped,
    WrongProposalKind,
)
from core.repositories.feeds import FeedRepository
from core.repositories.transformations import KeyPolicyRepository, TransformationRepository
from tests.configured import (
    a_registered_feed,
    approved_mapping,
    approved_transformation,
    mapping_proposal,
    transformation_proposal,
)

TRIM = [{"op": "trim"}]
CAST = [{"op": "trim"}, {"op": "cast", "type": "decimal"}]


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def feed(s):
    f = a_registered_feed(s, "syn_repo_transform")
    approved_mapping(s, f, "px", "close_price")
    approved_mapping(s, f, "qty", "volume")
    return f


def test_a_version_is_written_from_an_approved_proposal(s, feed):
    t = approved_transformation(s, feed, "px", TRIM)
    assert (t.feed_id, t.source_field, t.version) == (feed, "px", 1)
    assert [x.op for x in t.steps] == ["trim"]
    assert t.created_by == "dev-data-steward"


def test_versions_count_per_column(s, feed):
    assert approved_transformation(s, feed, "px", TRIM).version == 1
    assert approved_transformation(s, feed, "px", CAST).version == 2
    assert approved_transformation(s, feed, "qty", TRIM).version == 1
    repo = TransformationRepository(s)
    assert [t.version for t in repo.history(feed, "px")] == [1, 2]


def test_current_for_feed_keeps_the_latest_version_of_each_column(s, feed):
    approved_transformation(s, feed, "px", TRIM)
    latest = approved_transformation(s, feed, "px", CAST)
    other = approved_transformation(s, feed, "qty", TRIM)
    current = TransformationRepository(s).current_for_feed(feed)
    assert {t.id for t in current} == {latest.id, other.id}
    assert TransformationRepository(s).current_for_feed(a_registered_feed(s, "syn_repo_transform_2")) == []


def test_a_pending_proposal_is_refused(s, feed):
    proposal = transformation_proposal(s, feed, "px", TRIM)
    with pytest.raises(ProposalNotApproved):
        TransformationRepository(s).add_version(feed_id=feed, source_field="px", steps=TRIM, proposal_id=proposal.id,
                                                created_by="dev-data-steward")


def test_another_kind_is_refused(s, feed):
    proposal = mapping_proposal(s, feed, "px", ["close_price"], decision="approved")
    with pytest.raises(WrongProposalKind):
        TransformationRepository(s).add_version(feed_id=feed, source_field="px", steps=TRIM, proposal_id=proposal.id,
                                                created_by="dev-data-steward")


@pytest.mark.parametrize(("field", "steps"), [("qty", TRIM), ("px", CAST)])
def test_the_column_and_steps_must_be_the_proposals(s, feed, field, steps):
    proposal = transformation_proposal(s, feed, "px", TRIM, decision="approved")
    with pytest.raises(ProposalForAnotherField):
        TransformationRepository(s).add_version(feed_id=feed, source_field=field, steps=steps,
                                                proposal_id=proposal.id, created_by="dev-data-steward")


def test_an_unmapped_column_is_refused(s, feed):
    proposal = transformation_proposal(s, feed, "ghost", TRIM, decision="approved")
    with pytest.raises(SourceFieldNotMapped):
        TransformationRepository(s).add_version(feed_id=feed, source_field="ghost", steps=TRIM,
                                                proposal_id=proposal.id, created_by="dev-data-steward")


def test_an_unregistered_feed_is_refused(s):
    candidate = FeedRepository(s).create_candidate(code="syn_repo_transform_c", name="C").id
    proposal = transformation_proposal(s, candidate, "px", TRIM, decision="approved", register=False)
    with pytest.raises(FeedNotRegistered):
        TransformationRepository(s).add_version(feed_id=candidate, source_field="px", steps=TRIM,
                                                proposal_id=proposal.id, created_by="dev-data-steward")


def test_a_steps_list_outside_the_set_is_refused_before_anything_is_written(s, feed):
    from pydantic import ValidationError

    proposal = transformation_proposal(s, feed, "px", [{"op": "upper"}], decision="approved")
    with pytest.raises(ValidationError):
        TransformationRepository(s).add_version(feed_id=feed, source_field="px", steps=[{"op": "upper"}],
                                                proposal_id=proposal.id, created_by="dev-data-steward")


# ---------------------------------------------------------------- key policies


def test_no_policy_is_none(s, feed):
    assert KeyPolicyRepository(s).current(feed) is None


def test_policies_are_versioned_and_the_latest_is_current(s, feed):
    repo = KeyPolicyRepository(s)
    first = repo.set(feed, "flag", ["a"], "dev-data-steward", "Default.")
    second = repo.set(feed, "keep_last", ["a", "b"], "dev-data-steward", "Newest row wins.")
    assert (first.version, second.version) == (1, 2)
    current = repo.current(feed)
    assert (current.id, current.policy, current.key_columns) == (second.id, "keep_last", ["a", "b"])
    assert repo.current(a_registered_feed(s, "syn_repo_policy_2")) is None


def test_a_policy_for_a_missing_or_unregistered_feed_is_refused(s):
    import uuid

    repo = KeyPolicyRepository(s)
    with pytest.raises(FeedNotFound):
        repo.set(uuid.uuid4(), "flag", ["a"], "dev-data-steward", "x")
    candidate = FeedRepository(s).create_candidate(code="syn_repo_policy_c", name="C").id
    with pytest.raises(FeedNotRegistered):
        repo.set(candidate, "flag", ["a"], "dev-data-steward", "x")


def test_a_bad_policy_is_refused(s, feed):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        KeyPolicyRepository(s).set(feed, "drop", ["a"], "dev-data-steward", "x")
