"""Migration 0016's guards (phase 5 plan, section 6), by raw SQL: the repositories are bypassed on purpose.

Each refusal goes through answered_by(): the statement is refused with the guard's message, and with only
that guard removed (inside a savepoint rolled back at once) it is no longer refused with that message.
Every check on a nullable value has its NULL case.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository
from tests.configured import (
    a_registered_feed,
    alias,
    glossary_proposal,
    mapping_proposal,
    new_element,
)
from tests.test_db_onboarding_guards import _branch_off, answered_by, refused

GLENN = "dev-glossary-owner"


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def feed(s):
    return a_registered_feed(s, "syn_guards")


# ---------------------------------------------------------------- proposals: decided once; no TRUNCATE; one pending per column

STATUS = "UPDATE proposals SET status = :status, decided_at = now() WHERE id = :id"


@pytest.mark.parametrize("status", ["rejected", "approved"])
def test_a_decided_proposal_never_changes_again(s, feed, status):
    proposal = mapping_proposal(s, feed, "px", ["close_price"], decision="approved")
    answered_by(s, "a decided proposal never changes again", STATUS, {"status": status, "id": proposal.id},
                _branch_off("amaze_guard_proposal_change",
                            "proposals: proposal % is %, and a decided proposal never changes again"))


def test_a_pending_proposal_moves_only_to_approved_or_rejected(s, feed):
    proposal = mapping_proposal(s, feed, "px", ["close_price"])
    answered_by(s, "moves only to approved or rejected", STATUS, {"status": "withdrawn", "id": proposal.id},
                _branch_off("amaze_guard_proposal_change",
                            "proposals: a pending proposal moves only to approved or rejected, not %"))


def test_the_decision_path_still_moves_pending_to_approved_or_rejected(s, feed):
    for status in ("approved", "rejected"):
        proposal = mapping_proposal(s, feed, f"px_{status}", ["close_price"])
        s.execute(text(STATUS), {"status": status, "id": proposal.id})


def test_truncate_of_proposals_is_refused(s, feed):
    mapping_proposal(s, feed, "px", ["close_price"])
    answered_by(s, "proposals: TRUNCATE refused", "TRUNCATE proposals CASCADE", {},
                "DROP TRIGGER trg_proposals_no_truncate ON proposals")


def test_one_pending_proposal_per_kind_feed_and_column(s, feed):
    first = mapping_proposal(s, feed, "px", ["close_price"])
    second = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
              "SELECT proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id FROM proposals "
              "WHERE id = :id")
    answered_by(s, "uq_proposals_one_pending_per_feed_column", second, {"id": first.id},
                "DROP INDEX uq_proposals_one_pending_per_feed_column")


PROPOSAL = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
            "VALUES (:log, :kind, CAST(:payload AS jsonb), 'A2', ARRAY[:role], :feed)")


@pytest.mark.parametrize(("kind", "role"), [("mapping", "data_steward"), ("glossary_element", "glossary_owner")])
@pytest.mark.parametrize("payload", ['{"candidates": ["close_price"]}', '{}', 'null', '"source_field"'])
def test_a_glossary_or_mapping_proposal_names_its_column(s, feed, kind, role, payload):
    from tests.configured import logged_call

    log = logged_call(s, "MappingAgent", "mapping").id
    answered_by(s, "ck_proposals_names_a_column", PROPOSAL,
                {"log": log, "kind": kind, "payload": payload, "role": role, "feed": feed},
                "ALTER TABLE proposals DROP CONSTRAINT ck_proposals_names_a_column")


def test_other_kinds_need_no_column(s, feed):
    from tests.configured import logged_call

    s.execute(text(PROPOSAL), {"log": logged_call(s, "ProfilerAgent", "profiler").id, "kind": "profile_summary",
                               "payload": '{"recommended_outcome": "meets"}', "role": "backend_team", "feed": feed})


def test_another_column_another_kind_or_a_decided_one_may_be_pending_beside_it(s, feed):
    mapping_proposal(s, feed, "px", ["close_price"], decision="rejected")
    mapping_proposal(s, feed, "px", ["close_price"])
    mapping_proposal(s, feed, "qty", ["volume"])
    glossary_proposal(s, [new_element("px_thing")], feed_id=feed, column="px")


# ---------------------------------------------------------------- glossary_elements: an option of its proposal

ROW = ("INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, aliases, created_by, "
       "proposal_id) VALUES (:key, :version, :name, :domain, :data_type, :definition, :aliases, :by, :p)")
NONE_OF = _branch_off("amaze_require_glossary_option", "glossary element % version % is none of the options of proposal %")
NOT_THE_OWNER = _branch_off("amaze_require_glossary_option",
                            "glossary element %: created_by % is not the glossary_owner who approved proposal %")


def element_row(proposal_id, **more):
    row = {"key": "score_year", "version": 1, "name": "Score year", "domain": "pricing", "data_type": "integer",
           "definition": "Synthetic definition.", "aliases": [], "by": GLENN, "p": proposal_id}
    return {**row, **more}


@pytest.fixture
def new_score_year(s):
    return glossary_proposal(s, [new_element("score_year", name="Score year", data_type="integer")], decision="approved")


def test_the_new_element_as_proposed_is_accepted(s, new_score_year):
    s.execute(text(ROW), element_row(new_score_year.id))


@pytest.mark.parametrize("change", [
    {"name": "Score yr"}, {"domain": "security_master"}, {"data_type": "string"}, {"definition": "Other."},
    {"aliases": ["year"]}, {"version": 2}, {"key": "score_yr"},
])
def test_a_new_element_that_differs_from_its_option_is_refused(s, new_score_year, change):
    answered_by(s, "is none of the options of proposal", ROW, element_row(new_score_year.id, **change), NONE_OF)


def test_a_row_written_by_someone_but_the_approving_owner_is_refused(s, new_score_year):
    answered_by(s, "is not the glossary_owner who approved", ROW, element_row(new_score_year.id, by="dev-data-steward"),
                NOT_THE_OWNER)


def test_a_rejected_proposal_has_no_approver_to_write_its_row(s):
    """0005 answers first (rejected); without it, this guard still refuses: no approving owner."""
    rejected = glossary_proposal(s, [new_element("score_year", name="Score year", data_type="integer")],
                                 decision="rejected")
    refused(s, "is rejected, not approved", ROW, **element_row(rejected.id))


@pytest.fixture
def isin(s):
    return GlossaryRepository(s).get_current("isin")  # the seed's: aliases isin_code, isin_id


def alias_row(isin, proposal_id, **more):
    row = {"key": "isin", "version": isin.version + 1, "name": isin.name, "domain": isin.domain,
           "data_type": isin.data_type, "definition": isin.definition, "aliases": [*isin.aliases, "isin_number"],
           "by": GLENN, "p": proposal_id}
    return {**row, **more}


def test_the_alias_as_proposed_is_the_next_version(s, isin):
    proposal = glossary_proposal(s, [alias("isin", "isin_number")], decision="approved")
    s.execute(text(ROW), alias_row(isin, proposal.id))


@pytest.mark.parametrize("change", [
    {"version": 3}, {"version": 1}, {"aliases": ["isin_number", "isin_code", "isin_id"]},
    {"aliases": ["isin_code", "isin_id"]}, {"aliases": ["isin_number"]}, {"definition": "Changed."}, {"name": "Isin"},
    {"data_type": "integer"},
])
def test_an_alias_row_that_is_not_the_last_version_plus_the_alias_is_refused(s, isin, change):
    proposal = glossary_proposal(s, [alias("isin", "isin_number")], decision="approved")
    answered_by(s, "is none of the options of proposal", ROW, alias_row(isin, proposal.id, **change), NONE_OF)


def test_null_options_and_a_null_alias_match_nothing(s, isin, feed, approved_proposal):
    no_options = approved_proposal(kind="glossary_element", roles=("glossary_owner",), feed_id=feed,
                                   payload={"source_field": "isin_no"})
    answered_by(s, "is none of the options of proposal", ROW, alias_row(isin, no_options.id), NONE_OF)
    null_alias = approved_proposal(kind="glossary_element", roles=("glossary_owner",), feed_id=feed,
                                   payload={"source_field": "isin_no2", "options": [
                                       {"kind": "alias", "key": "isin", "alias": None, "element": None}]})
    answered_by(s, "is none of the options of proposal", ROW,
                alias_row(isin, null_alias.id, aliases=[*isin.aliases, None]), NONE_OF)


def test_one_glossary_row_per_proposal(s, isin):
    cusip = GlossaryRepository(s).get_current("cusip")
    two = glossary_proposal(s, [alias("isin", "sec_code"), alias("cusip", "sec_code")], decision="approved")
    s.execute(text(ROW), alias_row(isin, two.id, aliases=[*isin.aliases, "sec_code"]))
    answered_by(s, "uq_glossary_elements_proposal_id", ROW,
                {"key": "cusip", "version": 2, "name": cusip.name, "domain": cusip.domain, "data_type": cusip.data_type,
                 "definition": cusip.definition, "aliases": [*cusip.aliases, "sec_code"], "by": GLENN, "p": two.id},
                "DROP INDEX uq_glossary_elements_proposal_id")


def test_the_seed_needs_no_option(s):
    s.execute(text(ROW), element_row(None, key="seed_probe", by="seed"))


SEED_ONLY_NEW = _branch_off("amaze_require_glossary_option",
                            "glossary element % version %: a seed row is version 1 of a new key, with no proposal")


def test_a_seed_row_with_a_proposal_is_refused(s, new_score_year):
    """ck_glossary_elements_proposal_unless_seed would refuse it too; this guard answers first."""
    answered_by(s, "a seed row is version 1 of a new key", ROW, element_row(new_score_year.id, by="seed"),
                SEED_ONLY_NEW)


def test_a_seed_version_2_is_refused(s, isin):
    answered_by(s, "a seed row is version 1 of a new key", ROW, alias_row(isin, None, by="seed"), SEED_ONLY_NEW)


def test_a_seed_row_for_an_existing_key_is_refused(s, isin):
    """uq_glossary_elements_key_version would refuse version 1 too; this guard answers first."""
    answered_by(s, "a seed row is version 1 of a new key", ROW, alias_row(isin, None, by="seed", version=1),
                SEED_ONLY_NEW)


# ---------------------------------------------------------------- mappings: the row matches its proposal

MAP = ("INSERT INTO mappings (feed_id, source_field, element_key, version, proposal_id) "
       "VALUES (:feed, :field, :key, :version, :p)")
MATCHES = "amaze_require_mapping_matches_proposal"


def map_row(feed, proposal_id, **more):
    return {"feed": feed, "field": "px", "key": "close_price", "version": 1, "p": proposal_id, **more}


@pytest.fixture
def px(s, feed):
    return mapping_proposal(s, feed, "px", ["close_price", "mid_price"], decision="approved")


def test_a_mapping_as_proposed_is_accepted(s, feed, px):
    s.execute(text(MAP), map_row(feed, px.id, key="mid_price"))


def test_a_mapping_of_another_feed_is_refused(s, feed, px):
    other = a_registered_feed(s, "syn_guards_other")
    answered_by(s, "mapping: proposal .* is for feed", MAP, map_row(other, px.id),
                _branch_off(MATCHES, "mapping: proposal % is for feed %, not %"))


def test_a_mapping_from_a_proposal_with_no_feed_is_refused(s, feed, approved_proposal):
    no_feed = approved_proposal(kind="mapping", payload={"source_field": "px", "candidates": ["close_price"]})
    answered_by(s, "is for feed no feed", MAP, map_row(feed, no_feed.id),
                _branch_off(MATCHES, "mapping: proposal % is for feed %, not %"))


def test_a_mapping_of_another_column_is_refused(s, feed, approved_proposal):
    """A proposal with no source_field (the NULL case) cannot exist: ck_proposals_names_a_column (fix B)."""
    proposal = approved_proposal(kind="mapping", feed_id=feed, payload={"source_field": "qty",
                                                                        "candidates": ["close_price"]})
    answered_by(s, "mapping: proposal .* is for column", MAP, map_row(feed, proposal.id),
                _branch_off(MATCHES, "mapping: proposal % is for column %, not %"))


@pytest.mark.parametrize("candidates", [["mid_price"], None, "close_price", {"close_price": 1}])
def test_a_mapping_to_an_element_that_is_not_a_candidate_is_refused(s, feed, approved_proposal, candidates):
    payload = {"source_field": "px", **({} if candidates is None else {"candidates": candidates})}
    proposal = approved_proposal(kind="mapping", feed_id=feed, payload=payload)
    answered_by(s, "is not a candidate of proposal", MAP, map_row(feed, proposal.id),
                _branch_off(MATCHES, "mapping: % is not a candidate of proposal %"))


@pytest.mark.parametrize("version", [2, 0])
def test_a_mapping_that_is_not_the_columns_next_version_is_refused(s, feed, px, version):
    answered_by(s, "is not the next one", MAP, map_row(feed, px.id, version=version),
                _branch_off(MATCHES, "mapping: version % of column % is not the next one (%)"))


def test_a_mapping_of_a_feed_that_is_not_registered_is_refused(s, approved_proposal):
    candidate = FeedRepository(s).create_candidate(code="syn_guards_candidate", name="Candidate").id
    proposal = approved_proposal(kind="mapping", feed_id=candidate,
                                 payload={"source_field": "px", "candidates": ["close_price"]})
    answered_by(s, "is candidate, not registered", MAP, map_row(candidate, proposal.id),
                _branch_off(MATCHES, "mapping: feed % is %, not registered"))


def test_one_mapping_row_per_proposal(s, feed, px):
    s.execute(text(MAP), map_row(feed, px.id))
    answered_by(s, "uq_mappings_proposal_id", MAP, map_row(feed, px.id, version=2, key="mid_price"),
                "DROP INDEX uq_mappings_proposal_id")


# ---------------------------------------------------------------- glossary_runs: one way only; never deleted

RUN = "INSERT INTO glossary_runs (feed_id, profile_id, reason, requested_by, status, attempt) VALUES (:f, :p, 'requested', :by, :status, :attempt)"
RUN_GUARD = "amaze_guard_glossary_run"


@pytest.fixture
def run_feed(s, feed):
    return {"f": feed, "p": FeedProfileRepository(s).latest(feed).id, "by": GLENN}


def a_run(s, run_feed, *, status="queued", attempt=0, lease="interval '60 seconds'"):
    """A run moved through its states by UPDATEs the guard allows."""
    run = s.execute(text(RUN + " RETURNING id"), {**run_feed, "status": "queued", "attempt": 0}).scalar_one()
    if status != "queued":
        s.execute(text(f"UPDATE glossary_runs SET status = 'running', attempt = 1, lease_until = clock_timestamp() + "
                       f"{lease}, started_at = clock_timestamp() WHERE id = :id"), {"id": run})
    if status == "done":
        s.execute(text("UPDATE glossary_runs SET status = 'done', results = '{}', finished_at = clock_timestamp() "
                       "WHERE id = :id"), {"id": run})
    if status == "failed":
        s.execute(text("UPDATE glossary_runs SET status = 'failed', error = 'x', finished_at = clock_timestamp() "
                       "WHERE id = :id"), {"id": run})
    return run


@pytest.mark.parametrize(("status", "attempt"), [("running", 0), ("queued", 1), ("done", 0)])
def test_a_new_run_is_queued_at_attempt_0(s, run_feed, status, attempt):
    answered_by(s, "a new run is queued at attempt 0", RUN, {**run_feed, "status": status, "attempt": attempt},
                _branch_off(RUN_GUARD, "glossary_runs: a new run is queued at attempt 0, not % at %"))


MOVE = "UPDATE glossary_runs SET {set} WHERE id = :id"


@pytest.mark.parametrize(("start", "change"), [
    ("queued", "status = 'done', results = '{}', finished_at = clock_timestamp()"),  # skips running
    ("queued", "status = 'running', attempt = 2, lease_until = clock_timestamp()"),  # not attempt + 1
    ("running", "status = 'queued'"),
    ("running", "lease_until = clock_timestamp() + interval '1 hour'"),  # a new lease needs a new attempt
    ("running", "status = 'done', results = '{}', finished_at = clock_timestamp(), attempt = 2"),
])
def test_a_run_moves_only_queued_running_then_done_or_failed(s, run_feed, start, change):
    run = a_run(s, run_feed, status=start)
    answered_by(s, "cannot move from", MOVE.format(set=change), {"id": run},
                _branch_off(RUN_GUARD, "glossary_runs: run % cannot move from % (attempt %) to % (attempt %)"))


@pytest.mark.parametrize("final", ["done", "failed"])
def test_a_finished_run_never_changes(s, run_feed, final):
    run = a_run(s, run_feed, status=final)
    answered_by(s, "a finished run never changes", MOVE.format(set="error = 'later'"), {"id": run},
                _branch_off(RUN_GUARD, "glossary_runs: run % is %, and a finished run never changes"))


@pytest.mark.parametrize("change", ["reason = 'registered'", "requested_by = 'someone'", "feed_id = feed_id",
                                    "profile_id = gen_random_uuid()"])
def test_the_feed_profile_reason_and_requester_are_fixed(s, run_feed, change):
    run = a_run(s, run_feed)
    if change == "feed_id = feed_id":
        s.execute(text(MOVE.format(set=change)), {"id": run})  # no change is no change
        return
    answered_by(s, "are fixed", MOVE.format(set=change), {"id": run},
                _branch_off(RUN_GUARD, "glossary_runs: the feed, profile, reason and requester of run % are fixed"))


def test_columns_only_grow_and_only_while_queued(s, run_feed):
    run = a_run(s, run_feed)
    s.execute(text(MOVE.format(set="columns = ARRAY['maturity']")), {"id": run})
    message = _branch_off(RUN_GUARD, "glossary_runs: the columns of run % only grow while it is queued")
    answered_by(s, "only grow while it is queued", MOVE.format(set="columns = ARRAY['lot']"), {"id": run}, message)


def test_the_columns_of_a_running_run_are_fixed(s, run_feed):
    run = a_run(s, run_feed, status="running")
    answered_by(s, "only grow while it is queued", MOVE.format(set="columns = ARRAY['lot']"), {"id": run},
                _branch_off(RUN_GUARD, "glossary_runs: the columns of run % only grow while it is queued"))


def test_the_allowed_moves(s, run_feed):
    run = a_run(s, run_feed, status="running", lease="interval '0 seconds'")
    s.execute(text("UPDATE glossary_runs SET attempt = 2, lease_until = clock_timestamp() WHERE id = :id"), {"id": run})
    s.execute(text("UPDATE glossary_runs SET status = 'failed', error = 'x', finished_at = clock_timestamp() "
                   "WHERE id = :id"), {"id": run})


def test_a_run_is_never_deleted(s, run_feed):
    run = a_run(s, run_feed)
    answered_by(s, "glossary_runs: DELETE refused", "DELETE FROM glossary_runs WHERE id = :id", {"id": run},
                _branch_off(RUN_GUARD, "glossary_runs: DELETE refused; a run is history"))


def test_truncate_of_glossary_runs_is_refused(s, run_feed):
    a_run(s, run_feed)
    answered_by(s, "glossary_runs: TRUNCATE refused", "TRUNCATE glossary_runs", {},
                "DROP TRIGGER trg_glossary_runs_no_truncate ON glossary_runs")


def test_one_active_run_per_feed(s, run_feed):
    a_run(s, run_feed)
    answered_by(s, "uq_glossary_runs_one_active_per_feed", RUN, {**run_feed, "status": "queued", "attempt": 0},
                "DROP INDEX uq_glossary_runs_one_active_per_feed")


def test_a_finished_run_lets_another_be_active(s, run_feed):
    a_run(s, run_feed, status="done")
    a_run(s, run_feed)


@pytest.mark.parametrize(("constraint", "change"), [
    ("ck_glossary_runs_running_has_a_lease", "status = 'running', attempt = 1, lease_until = NULL"),
    ("ck_glossary_runs_failed_has_an_error", "status = 'failed', attempt = 1, error = NULL, finished_at = now()"),
    ("ck_glossary_runs_done_has_results", "status = 'done', attempt = 1, results = NULL, finished_at = now()"),
    ("ck_glossary_runs_finished_at_matches_status", "status = 'done', attempt = 1, results = '{}', finished_at = NULL"),
    ("ck_glossary_runs_call_only_when_done", "status = 'running', attempt = 1, lease_until = now(), "
                                             "proposal_log_id = (SELECT id FROM proposal_log LIMIT 1)"),
])
def test_each_status_carries_what_it_needs_null_cases(s, run_feed, constraint, change):
    """Each update starts from a state the guard lets it leave: queued for running; running for done/failed."""
    from tests.configured import logged_call

    logged_call(s, "GlossaryBuilderAgent", "glossary_builder")
    start = "queued" if change.startswith("status = 'running'") else "running"
    run = a_run(s, run_feed, status=start)
    answered_by(s, constraint, MOVE.format(set=change), {"id": run},
                f"ALTER TABLE glossary_runs DROP CONSTRAINT {constraint}")


def test_the_payload_shape_the_guards_read_is_the_domain_models(s, feed):
    """The guards read options[].element and candidates as the domain models dump them."""
    proposal = glossary_proposal(s, [new_element("score_year", name="Score year", data_type="integer")])
    stored = s.execute(text("SELECT payload FROM proposals WHERE id = :id"), {"id": proposal.id}).scalar_one()
    assert set(stored["options"][0]["element"]) == {"key", "name", "domain", "data_type", "definition", "aliases"}
    assert json.loads(json.dumps(stored["options"][0]["element"]["aliases"])) == []
    assert uuid.UUID(stored["run_id"])
