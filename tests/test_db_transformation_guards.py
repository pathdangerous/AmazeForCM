"""Migration 0023's guards (phase 7.8 plan, assumptions 27 to 29), by raw SQL: the repositories are bypassed.

Each refusal goes through answered_by(): the statement is refused with the guard's message, and with only that
guard removed (inside a savepoint rolled back at once) it is no longer refused with that message.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from core.repositories.feeds import FeedRepository
from tests.configured import (
    a_registered_feed,
    approved_mapping,
    logged_call,
    mapping_proposal,
    transformation_proposal,
)
from tests.test_db_onboarding_guards import _branch_off, answered_by, refused

TRIM = [{"op": "trim"}]
ROW = ("INSERT INTO transformations (feed_id, source_field, version, steps, proposal_id, created_by) "
       "VALUES (:feed, :field, :version, CAST(:steps AS jsonb), :proposal, :by)")


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def feed(s):
    f = a_registered_feed(s, "syn_transform_guards")
    approved_mapping(s, f, "px", "close_price")
    return f


def row(made, feed, **over):
    payload = made.payload
    return {"feed": feed, "field": payload["source_field"], "version": 1,
            "steps": json.dumps(payload.get("steps", TRIM)), "proposal": made.id, "by": "dev-data-steward", **over}


def matches(message):
    return _branch_off("amaze_require_transformation_matches_proposal", message)


def approved(message):
    return _branch_off("amaze_require_approved_proposal", message)


# ---------------------------------------------------------------- the legal path

def test_a_row_as_approved_is_accepted(s, feed):
    proposal = transformation_proposal(s, feed, "px", TRIM, decision="approved")
    s.execute(text(ROW), row(proposal, feed))


def test_every_op_in_order_is_accepted(s, feed):
    steps = [{"op": "trim"}, {"op": "placeholder_to_null", "values": ["N/A", "-"]},
             {"op": "parse_date", "format": "DD-MM-YYYY"}, {"op": "cast", "type": "date"},
             {"op": "rename", "to": "price_date"}]
    proposal = transformation_proposal(s, feed, "px", steps, decision="approved")
    s.execute(text(ROW), row(proposal, feed))


# ---------------------------------------------------------------- an approved transformation proposal

def test_no_proposal_is_refused(s, feed):
    proposal = transformation_proposal(s, feed, "px", TRIM, decision="approved")
    answered_by(s, "needs an approved transformation proposal", ROW, row(proposal, feed, proposal=None),
                approved("% row needs an approved % proposal"))


@pytest.mark.parametrize("case", ["pending", "rejected", "other kind", "other feed"])
def test_a_proposal_that_is_not_approved_for_this_feed_is_refused(s, feed, case):
    if case == "pending":
        proposal = transformation_proposal(s, feed, "px", TRIM)
        match, removal = "is pending, not approved", approved("proposal % is %, not approved")
    elif case == "rejected":
        proposal = transformation_proposal(s, feed, "px", TRIM, decision="rejected")
        match, removal = "is rejected, not approved", approved("proposal % is %, not approved")
    elif case == "other kind":
        proposal = mapping_proposal(s, feed, "px", ["close_price"], decision="approved")
        match, removal = "is a mapping proposal, not transformation", approved("proposal % is a % proposal, not %")
        params = {"feed": feed, "field": "px", "version": 1, "steps": json.dumps(TRIM), "proposal": proposal.id,
                  "by": "dev-data-steward"}
        answered_by(s, match, ROW, params, removal)
        return
    else:
        other = a_registered_feed(s, "syn_transform_other")
        proposal = transformation_proposal(s, other, "px", TRIM, decision="approved")
        answered_by(s, "is for feed", ROW, row(proposal, feed),
                    matches("transformation: proposal % is for feed %, not %"))
        return
    answered_by(s, match, ROW, row(proposal, feed), removal)


def test_the_column_must_be_the_proposals(s, feed):
    approved_mapping(s, feed, "qty", "volume")
    proposal = transformation_proposal(s, feed, "px", TRIM, decision="approved")
    answered_by(s, "is for column", ROW, row(proposal, feed, field="qty"),
                matches("transformation: proposal % is for column %, not %"))


@pytest.mark.parametrize("steps", [[{"op": "trim"}, {"op": "cast", "type": "string"}], [{"op": "cast", "type": "string"}]])
def test_the_steps_must_be_the_proposals(s, feed, steps):
    proposal = transformation_proposal(s, feed, "px", TRIM, decision="approved")
    answered_by(s, "holds other steps", ROW, row(proposal, feed, steps=json.dumps(steps)),
                matches("transformation: proposal % holds other steps than the row"))


def test_a_proposal_without_steps_matches_no_row(s, feed):
    """The NULL case: a missing steps key reads as NULL, which must not match the row's steps."""
    proposal = transformation_proposal(s, feed, "px", TRIM, decision="approved", payload={"source_field": "px"})
    answered_by(s, "holds other steps", ROW, row(proposal, feed, steps=json.dumps(TRIM)),
                matches("transformation: proposal % holds other steps than the row"))


def test_the_version_is_the_columns_next_one(s, feed):
    first = transformation_proposal(s, feed, "px", TRIM, decision="approved")
    answered_by(s, "is not the next one", ROW, row(first, feed, version=2),
                matches("transformation: version % of column % is not the next one (%)"))
    s.execute(text(ROW), row(first, feed))
    second = transformation_proposal(s, feed, "px", [{"op": "cast", "type": "string"}], decision="approved")
    answered_by(s, "is not the next one", ROW, row(second, feed, version=1),
                matches("transformation: version % of column % is not the next one (%)"))
    s.execute(text(ROW), row(second, feed, version=2))


def test_a_feed_that_is_not_registered_is_refused(s):
    candidate = FeedRepository(s).create_candidate(code="syn_transform_candidate", name="Candidate").id
    proposal = transformation_proposal(s, candidate, "px", TRIM, decision="approved", register=False)
    answered_by(s, "not registered", ROW, row(proposal, candidate),
                matches("transformation: feed % is %, not registered"))


def test_a_column_that_is_not_mapped_is_refused(s, feed):
    proposal = transformation_proposal(s, feed, "ghost", TRIM, decision="approved")
    answered_by(s, "is not mapped", ROW, row(proposal, feed),
                matches("transformation: column % of feed % is not mapped"))


def test_two_rows_never_come_from_one_proposal(s, feed):
    proposal = transformation_proposal(s, feed, "px", TRIM, decision="approved")
    s.execute(text(ROW), row(proposal, feed))
    answered_by(s, "uq_transformations_proposal_id", ROW, row(proposal, feed, version=2),
                "DROP INDEX uq_transformations_proposal_id")


def test_a_row_names_its_creator(s, feed):
    proposal = transformation_proposal(s, feed, "px", TRIM, decision="approved")
    answered_by(s, "ck_transformations_created_by", ROW, row(proposal, feed, by=" "),
                "ALTER TABLE transformations DROP CONSTRAINT ck_transformations_created_by")


# ---------------------------------------------------------------- the JSON CHECK

DROP_STEPS = "ALTER TABLE transformations DROP CONSTRAINT ck_transformations_steps"
BAD_STEPS = [
    [], [{"op": "upper"}], [{"op": "trim"}] * 7, [{"op": "trim"}, {"op": "trim"}],
    [{"op": "parse_date", "format": "YYYY/MM/DD"}], [{"op": "parse_date"}], [{"op": "cast", "type": "float"}],
    [{"op": "rename", "to": "px"}, {"op": "trim"}], [{"op": "rename", "to": "Px"}], [{"op": "rename", "to": "a" * 64}],
    [{"op": "trim", "extra": 1}], [{"op": "placeholder_to_null"}], [{"op": "placeholder_to_null", "values": []}],
    [{"op": "placeholder_to_null", "values": [str(n) for n in range(11)]}],
    [{"op": "placeholder_to_null", "values": ["x" * 17]}], [{"op": "placeholder_to_null", "values": [5]}],
    [{"op": "placeholder_to_null", "values": "NA"}], {"op": "trim"}, "trim", None, [None], ["trim"], [{"op": None}],
    [{"op": 5}], [{}],
]


@pytest.mark.parametrize("steps", BAD_STEPS, ids=lambda v: json.dumps(v)[:60])
def test_an_op_outside_the_set_or_a_malformed_step_is_refused(s, feed, steps):
    proposal = transformation_proposal(s, feed, "px", steps, decision="approved")
    answered_by(s, "ck_transformations_steps", ROW, row(proposal, feed, steps=json.dumps(steps)), DROP_STEPS)


# ---------------------------------------------------------------- append-only, one pending proposal, naming the column

@pytest.mark.parametrize("statement", [
    "UPDATE transformations SET created_at = created_at",
    "DELETE FROM transformations",
    "TRUNCATE transformations CASCADE",
])
def test_transformations_are_append_only(s, feed, statement):
    proposal = transformation_proposal(s, feed, "px", TRIM, decision="approved")
    s.execute(text(ROW), row(proposal, feed))
    refused(s, "transformations is append-only", statement)


COPY = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
        "SELECT proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id FROM proposals "
        "WHERE id = :id")


def test_one_pending_proposal_per_feed_and_column(s, feed):
    first = transformation_proposal(s, feed, "px", TRIM)
    answered_by(s, "uq_proposals_one_pending_transformation_per_feed_column", COPY, {"id": first.id},
                "DROP INDEX uq_proposals_one_pending_transformation_per_feed_column")


def test_another_column_or_a_decided_proposal_may_be_pending_beside_it(s, feed):
    transformation_proposal(s, feed, "px", TRIM, decision="rejected")
    transformation_proposal(s, feed, "px", TRIM)
    transformation_proposal(s, feed, "qty", TRIM)


PROPOSAL = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
            "VALUES (:log, 'transformation', CAST(:payload AS jsonb), 'A2', ARRAY['data_steward'], :feed)")
DROP_NAMES = "ALTER TABLE proposals DROP CONSTRAINT ck_proposals_names_a_transformation"


@pytest.mark.parametrize("payload", [
    '{}', 'null', '"source_field"', '{"steps": []}', '{"source_field": null}', '{"source_field": 5}',
    '{"source_field": ["px"]}', '{"source_field": ""}', '{"source_field": "  "}'])
def test_a_transformation_proposal_names_its_column(s, feed, payload):
    log = logged_call(s, "TransformationAgent", "transformation").id
    answered_by(s, "ck_proposals_names_a_transformation", PROPOSAL, {"log": log, "payload": payload, "feed": feed},
                DROP_NAMES)


def test_a_transformation_proposal_has_a_feed(s, feed):
    params = {"log": logged_call(s, "TransformationAgent", "transformation").id,
              "payload": '{"source_field": "px", "steps": []}'}
    answered_by(s, "ck_proposals_names_a_transformation", PROPOSAL, {**params, "feed": None}, DROP_NAMES)
    s.execute(text(PROPOSAL), {**params, "feed": feed})


# ---------------------------------------------------------------- feed_key_policies

POLICY = ("INSERT INTO feed_key_policies (feed_id, version, policy, key_columns, set_by, reason) "
          "VALUES (:feed, :version, :policy, CAST(:cols AS text[]), :by, :reason)")


def policy(feed, **over):
    return {"feed": feed, "version": 1, "policy": "flag", "cols": ["a"], "by": "dev-data-steward",
            "reason": "Synthetic.", **over}


def test_a_policy_is_accepted(s, feed):
    for number, name in enumerate(("hold", "keep_last", "flag"), start=1):
        s.execute(text(POLICY), policy(feed, version=number, policy=name, cols=list("abcde")[:number + 2]))


@pytest.mark.parametrize(("over", "constraint"), [
    ({"policy": "drop"}, "ck_feed_key_policies_policy"),
    ({"cols": []}, "ck_feed_key_policies_key_columns"),
    ({"cols": list("abcdef")}, "ck_feed_key_policies_key_columns"),
    ({"cols": ["a", " "]}, "ck_feed_key_policies_key_columns"),
    ({"cols": [""]}, "ck_feed_key_policies_key_columns"),
    ({"cols": ["a", "a"]}, "ck_feed_key_policies_key_columns"),
    ({"version": 0}, "ck_feed_key_policies_version"),
    ({"by": " "}, "ck_feed_key_policies_set_by"),
    ({"reason": " "}, "ck_feed_key_policies_reason"),
])
def test_a_bad_policy_is_refused(s, feed, over, constraint):
    answered_by(s, constraint, POLICY, policy(feed, **over),
                f"ALTER TABLE feed_key_policies DROP CONSTRAINT {constraint}")


def test_a_policy_needs_a_registered_feed(s):
    candidate = FeedRepository(s).create_candidate(code="syn_policy_candidate", name="Candidate").id
    answered_by(s, "not registered", POLICY, policy(candidate),
                _branch_off("amaze_guard_feed_key_policy", "key policy: feed % is %, not registered"))


def test_a_policy_version_is_unique_per_feed(s, feed):
    s.execute(text(POLICY), policy(feed))
    answered_by(s, "uq_feed_key_policies_feed_id_version", POLICY, policy(feed),
                "ALTER TABLE feed_key_policies DROP CONSTRAINT uq_feed_key_policies_feed_id_version")
    s.execute(text(POLICY), policy(feed, version=2))


@pytest.mark.parametrize("statement", [
    "UPDATE feed_key_policies SET set_at = set_at",
    "DELETE FROM feed_key_policies",
    "TRUNCATE feed_key_policies CASCADE",
])
def test_policies_are_append_only(s, feed, statement):
    s.execute(text(POLICY), policy(feed))
    refused(s, "feed_key_policies is append-only", statement)
