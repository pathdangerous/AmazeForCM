"""Migration 0018's rule guards (phase 6 plan, section 6: G1, G2, G3, G5), by raw SQL.

The repositories are bypassed on purpose, so only the database decides. Each refusal goes through
answered_by(): the statement is refused with the guard's message, and with only that guard removed (inside a
savepoint rolled back at once) it is no longer refused with that message. Every check on a nullable value has
its NULL case. Each test starts with a refusal, so against 0017 (no guard) it fails.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from core.repositories.feeds import FeedRepository
from core.repositories.proposals import ProposalRepository
from tests.configured import a_registered_feed, logged_call
from tests.conftest import dev_user_for
from tests.onboarded import registered_feed
from tests.test_db_onboarding_guards import _branch_off, answered_by

STEWARD_THEN_CHECKER = ["data_steward", "checker"]
SQL = "SELECT _row FROM curated WHERE close_price IS NULL"


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def feed(s):
    return a_registered_feed(s, "syn_rule_guards")


def rule_proposal(s, feed, *, key="close_price_present", decision=None, payload=None):
    """A rule proposal of the real shape on a logged call; decided by the steward and the checker (or
    rejected by the steward) if asked."""
    payload = payload if payload is not None else {"rule_key": key, "sql": SQL, "description": "Price is present."}
    repo = ProposalRepository(s)
    proposal = repo.create(proposal_log_id=logged_call(s, "RuleRecommenderAgent", "rule_recommender").id,
                           kind="rule", payload=payload, autonomy="A2", required_approver_roles=STEWARD_THEN_CHECKER,
                           feed_id=feed)
    if decision == "approved":
        for role in STEWARD_THEN_CHECKER:
            repo.record_decision(proposal.id, user=dev_user_for(role), decision="approved")
    elif decision == "rejected":
        repo.record_decision(proposal.id, user=dev_user_for("data_steward"), decision="rejected", note="Synthetic.")
    return repo.get(proposal.id)


# ---------------------------------------------------------------- G1: a rule proposal names its rule

PROPOSAL = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
            "VALUES (:log, :kind, CAST(:payload AS jsonb), 'A2', ARRAY['data_steward', 'checker'], :feed)")
DROP_NAMES_A_RULE = "ALTER TABLE proposals DROP CONSTRAINT ck_proposals_names_a_rule"


@pytest.mark.parametrize("payload", [
    '{}', 'null', '"rule_key"', '{"sql": "SELECT 1"}', '{"rule_key": "k"}',
    # the JSON null and wrong-type cases of each key (a missing key reads as NULL, and NULL passes a check)
    '{"rule_key": null, "sql": "SELECT 1"}', '{"rule_key": 5, "sql": "SELECT 1"}',
    '{"rule_key": ["k"], "sql": "SELECT 1"}', '{"rule_key": "", "sql": "SELECT 1"}',
    '{"rule_key": "  ", "sql": "SELECT 1"}', '{"rule_key": "k", "sql": null}', '{"rule_key": "k", "sql": 5}',
    '{"rule_key": "k", "sql": ""}', '{"rule_key": "k", "sql": "  "}'])
def test_a_rule_proposal_names_its_rule_key_and_sql(s, feed, payload):
    from tests.configured import logged_call as call

    params = {"log": call(s, "RuleRecommenderAgent", "rule_recommender").id, "kind": "rule", "payload": payload,
              "feed": feed}
    answered_by(s, "ck_proposals_names_a_rule", PROPOSAL, params, DROP_NAMES_A_RULE)


def test_a_rule_proposal_has_a_feed(s, feed):
    """The NULL case of feed_id: with no feed the one-pending-per-key index never conflicts (NULLs are distinct).
    The same payload with the feed is accepted."""
    params = {"log": logged_call(s, "RuleRecommenderAgent", "rule_recommender").id, "kind": "rule",
              "payload": '{"rule_key": "k", "sql": "SELECT 1"}'}
    answered_by(s, "ck_proposals_names_a_rule", PROPOSAL, {**params, "feed": None}, DROP_NAMES_A_RULE)
    s.execute(text(PROPOSAL), {**params, "feed": feed})


def test_other_kinds_need_no_rule_key(s, feed):
    """A rule proposal with a payload that lacks the key is refused (so this test fails against 0017); a kind
    that is not a rule is not asked for one."""
    params = {"log": logged_call(s, "RuleRecommenderAgent", "rule_recommender").id, "payload": "{}", "feed": feed}
    answered_by(s, "ck_proposals_names_a_rule", PROPOSAL, {**params, "kind": "rule"}, DROP_NAMES_A_RULE)
    s.execute(text(PROPOSAL.replace("ARRAY['data_steward', 'checker']", "ARRAY['data_owner']")),
              {**params, "kind": "review_summary"})


# ---------------------------------------------------------------- G2: one pending proposal per feed and rule key

DROP_ONE_PENDING_RULE = "DROP INDEX uq_proposals_one_pending_rule_per_feed_key"
DROP_ONE_PENDING_SUMMARY = "DROP INDEX uq_proposals_one_pending_review_summary_per_feed"
SUMMARY = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
           "VALUES (:log, 'review_summary', CAST('{}' AS jsonb), 'A1', ARRAY['data_owner'], :feed)")


def test_one_pending_rule_proposal_per_feed_and_rule_key(s, feed):
    first = rule_proposal(s, feed)
    second = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
              "SELECT proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id FROM proposals "
              "WHERE id = :id")
    answered_by(s, "uq_proposals_one_pending_rule_per_feed_key", second, {"id": first.id}, DROP_ONE_PENDING_RULE)


def test_another_key_another_feed_or_a_decided_proposal_may_be_pending_beside_it(s, feed):
    first = rule_proposal(s, feed)
    duplicate = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, "
                 "feed_id) SELECT proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id "
                 "FROM proposals WHERE id = :id")
    answered_by(s, "uq_proposals_one_pending_rule_per_feed_key", duplicate, {"id": first.id}, DROP_ONE_PENDING_RULE)
    rule_proposal(s, feed, key="volume_not_negative")
    rule_proposal(s, a_registered_feed(s, "syn_rule_guards_other"))
    rejected = rule_proposal(s, feed, key="price_date_present", decision="rejected")
    rule_proposal(s, feed, key=rejected.payload["rule_key"])  # the decided one no longer counts


def test_one_pending_review_summary_per_feed(s, feed):
    s.execute(text(SUMMARY), {"log": logged_call(s, "ReviewSummariserAgent", "review_summariser").id, "feed": feed})
    answered_by(s, "uq_proposals_one_pending_review_summary_per_feed", SUMMARY,
                {"log": logged_call(s, "ReviewSummariserAgent", "review_summariser").id, "feed": feed},
                DROP_ONE_PENDING_SUMMARY)
    other = a_registered_feed(s, "syn_rule_guards_other")  # another feed may have its own
    s.execute(text(SUMMARY), {"log": logged_call(s, "ReviewSummariserAgent", "review_summariser").id, "feed": other})


# ---------------------------------------------------------------- G3: two rules from one proposal

RULE = ("INSERT INTO rules (feed_id, rule_key, version, sql, description, proposal_id) "
        "VALUES (:feed, :key, :version, :rule_sql, :description, :proposal)")  # not :sql: refused() has an sql argument


def params(proposal, feed, **over):
    payload = proposal.payload
    return {"feed": feed, "key": payload["rule_key"], "version": 1, "rule_sql": payload["sql"],
            "description": payload["description"], "proposal": proposal.id, **over}


def test_two_rules_never_come_from_one_proposal(s, feed):
    proposal = rule_proposal(s, feed, decision="approved")
    s.execute(text(RULE), params(proposal, feed))
    answered_by(s, "uq_rules_proposal_id", RULE, params(proposal, feed, version=2),
                "DROP INDEX uq_rules_proposal_id")


# ---------------------------------------------------------------- G5: a rule matches its approved proposal (note 5)


def matches(message):
    return _branch_off("amaze_require_rule_matches_proposal", message)


def test_a_rule_of_another_feed_than_its_proposal_is_refused(s, feed):
    proposal = rule_proposal(s, feed, decision="approved")
    other = a_registered_feed(s, "syn_rule_guards_other")
    answered_by(s, "is for feed", RULE, params(proposal, other),
                matches("rule: proposal % is for feed %, not %"))
    s.execute(text(RULE), params(proposal, feed))  # as proposed, it is accepted


def test_a_rule_with_another_key_than_its_proposal_is_refused(s, feed):
    proposal = rule_proposal(s, feed, decision="approved")
    answered_by(s, "is for rule", RULE, params(proposal, feed, key="volume_not_negative"),
                matches("rule: proposal % is for rule %, not %"))


def test_a_rule_with_other_sql_than_its_proposal_is_refused(s, feed):
    proposal = rule_proposal(s, feed, decision="approved")
    answered_by(s, "holds other SQL", RULE, params(proposal, feed, rule_sql="SELECT _row FROM curated"),
                matches("rule: proposal % holds other SQL than the row"))


def test_a_rule_with_another_description_than_its_proposal_is_refused(s, feed):
    proposal = rule_proposal(s, feed, decision="approved")
    answered_by(s, "holds another description", RULE, params(proposal, feed, description="Another."),
                matches("rule: proposal % holds another description than the row"))


@pytest.mark.parametrize("payload_description", [None, "absent"])
def test_a_proposal_with_no_description_matches_no_row(s, feed, payload_description):
    """The NULL case: description is not asked for by G1, so a proposal may lack it (a missing key and a JSON
    null both read as NULL). IS DISTINCT FROM keeps NULL from matching the row's text."""
    payload = {"rule_key": "close_price_present", "sql": SQL}
    if payload_description is None:
        payload["description"] = None
    proposal = rule_proposal(s, feed, payload=payload, decision="approved")
    answered_by(s, "holds another description", RULE, params(proposal, feed, description="Price is present."),
                matches("rule: proposal % holds another description than the row"))


def test_the_version_is_the_keys_next_one(s, feed):
    first = rule_proposal(s, feed, decision="approved")
    answered_by(s, "is not the next one", RULE, params(first, feed, version=2),
                matches("rule: version % of rule % is not the next one (%)"))
    s.execute(text(RULE), params(first, feed))
    second = rule_proposal(s, feed, payload={"rule_key": first.payload["rule_key"], "sql": "SELECT _row FROM curated",
                                             "description": "Version two."}, decision="approved")
    answered_by(s, "is not the next one", RULE, params(second, feed, version=1),
                matches("rule: version % of rule % is not the next one (%)"))
    s.execute(text(RULE), params(second, feed, version=2))


def test_a_rule_of_a_feed_that_is_not_registered_is_refused(s):
    candidate = FeedRepository(s).create_candidate(code="syn_rule_candidate", name="Candidate").id
    proposal = rule_proposal(s, candidate, decision="approved")
    answered_by(s, "not registered", RULE, params(proposal, candidate),
                matches("rule: feed % is %, not registered"))
    registered_feed(s, candidate)
    s.execute(text(RULE), params(proposal, candidate))
