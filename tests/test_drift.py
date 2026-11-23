"""Phase 7.12, T4: drift on a new delivery. Step 12 reports new columns, missing columns, changed order (information
only: the engine maps by name), type changes against the latest profile, and, after the run, rules whose flagged rate
rose against the last released load. Each finding comes with proposed actions, made by code (step 12 has no model), as
`profile_action` proposals with origin `rule`: a person selects them; the agents then propose updated mappings and rules;
the new go-live stores code version 2, and the diff from version 1 shows the change. A person may add an action too (T7).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from core.domain.manual import ManualProfileAction
from core.proposal_log import ProposalLogEntry
from core.repositories.code_versions import CodeVersionRepository
from core.repositories.loads import LoadRepository
from core.repositories.proposals import ProposalRepository
from core.engine.codegen import diff_versions
from core.steps.drift import (
    Finding,
    add_drift_action,
    drift_actions,
    drift_of_feed,
    rule_rate_rises,
    structure_drift,
)
from core.steps.s02_profile import decide_profile_action
from ports.connectors.dropzone import write_to_dropzone
from tests.test_onboarding import OPERATOR, approve, make_world, run_to, work

V2 = Path(__file__).resolve().parent.parent / "fixtures" / "sources" / "Synthetic_Security_Master_v2.xlsx"
TEMPLATE = [("a", "BIGINT"), ("b", "VARCHAR"), ("c", "DOUBLE")]


def test_structure_drift_reports_new_missing_reordered_and_retyped_columns():
    delivery = [("c", "DOUBLE"), ("b", "BIGINT"), ("a", "BIGINT"), ("d", "VARCHAR")]
    report = structure_drift(TEMPLATE, delivery)
    assert report.new_columns == ["d"] and report.missing_columns == []
    assert report.order_changed and report.order_after[0] == "c"
    assert [(t.column, t.was, t.now) for t in report.type_changes] == [("b", "VARCHAR", "BIGINT")]
    assert report.has_drift
    gone = structure_drift(TEMPLATE, [("a", "BIGINT"), ("b", "VARCHAR")])
    assert gone.missing_columns == ["c"] and gone.new_columns == [] and not gone.order_changed


def test_the_same_structure_is_no_drift_and_a_name_in_another_case_is_the_same_column():
    assert not structure_drift(TEMPLATE, TEMPLATE).has_drift
    assert not structure_drift(TEMPLATE, [("A", "BIGINT"), ("B", "VARCHAR"), ("C", "DOUBLE")]).has_drift


def test_each_finding_has_its_actions_and_a_changed_order_has_none():
    report = structure_drift(TEMPLATE, [("c", "DOUBLE"), ("b", "BIGINT"), ("a", "BIGINT"), ("d", "VARCHAR")])
    actions = {(a.action, a.columns[0]): a.applied_by for a in drift_actions(report)}
    assert actions == {("map_new_column", "d"): "agent", ("accept_type_change", "b"): "manual"}  # order: information only
    only_order = structure_drift(TEMPLATE, [("c", "DOUBLE"), ("b", "VARCHAR"), ("a", "BIGINT")])
    assert only_order.order_changed and drift_actions(only_order) == []
    missing = drift_actions(structure_drift(TEMPLATE, [("a", "BIGINT")]))
    assert {(a.action, a.applied_by) for a in missing} == {("review_missing_column", "manual")}


def test_a_rule_whose_flagged_rate_rose_is_reported_and_one_that_fell_or_is_new_is_not():
    last = {"r_up": (1, 100), "r_down": (10, 100), "r_same": (5, 50)}
    now = {"r_up": (10, 100), "r_down": (1, 100), "r_same": (5, 50), "r_new": (30, 100)}
    rises = rule_rate_rises(last, now)
    assert [(r.rule_key, r.was, r.now) for r in rises] == [("r_up", 0.01, 0.1)]
    assert rule_rate_rises({"r": (0, 0)}, {"r": (3, 10)}) == []  # no earlier rate to compare with


def test_the_finding_type_is_one_of_the_named_ones():
    assert {f.value for f in Finding} == {"new_column", "missing_column", "type_change", "rule_rate_rise"}


# ---------------------------------------------------------------- the whole path: v1 released, v2 delivered


def released(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "go_live")
    approve(w)
    work(w)  # the first load
    return w


def deliver_v2(w) -> dict:
    write_to_dropzone(w.settings.dropzone_root, "syn_security_master", V2.name, V2.read_bytes())
    return w.ctx.load_now(w.feed_id)


def test_the_second_delivery_reports_its_drift_proposes_actions_and_leads_to_code_version_two(db_session, glossary, tmp_path):
    w = released(db_session, glossary, tmp_path)
    [v1] = CodeVersionRepository(db_session).list_for_feed(w.feed_id)
    calls = db_session.scalar(select(func.count()).select_from(ProposalLogEntry))

    result = deliver_v2(w)

    assert result["fetch"] == "fetched"
    assert db_session.scalar(select(func.count()).select_from(ProposalLogEntry)) == calls  # step 12: no model
    latest = LoadRepository(db_session).list_for_feed(w.feed_id)[0]
    assert latest.arrival_outcome == "additive_parked"
    drift = latest.arrival_detail["drift"]
    assert drift["new_columns"] == ["BEST_FCF"] and drift["missing_columns"] == []
    assert drift["order_changed"] is True  # BEST_BPS and BEST_CAPEX swapped: information only

    view = drift_of_feed(db_session, w.feed_id)
    assert view.load_id == latest.id and view.report.new_columns == ["BEST_FCF"]
    [action] = [a for a in view.actions if a.action == "map_new_column"]
    assert (action.origin, action.applied_by, action.columns, action.status) == ("rule", "agent", ["BEST_FCF"], "pending")
    stored = ProposalRepository(db_session).get(action.proposal_id)
    assert (stored.origin, stored.proposal_log_id, stored.author, stored.kind) == ("rule", None, None, "profile_action")

    decide_profile_action(db_session, proposal_id=action.proposal_id, user=OPERATOR, decision="approved")
    assert any(a["proposal_id"] == str(action.proposal_id) and a["action"] == "map_new_column"
               for a in __import__("core.steps.s02_profile", fromlist=["x"]).selected_actions(db_session, w.feed_id))

    assert len(CodeVersionRepository(db_session).list_for_feed(w.feed_id)) == 1  # nothing new until the next go-live
    assert v1.version == 1


def test_a_person_adds_a_drift_action_by_hand(db_session, glossary, tmp_path):
    w = released(db_session, glossary, tmp_path)
    deliver_v2(w)
    before = db_session.scalar(select(func.count()).select_from(ProposalLogEntry))
    made = add_drift_action(db_session, feed_id=w.feed_id, user=OPERATOR, item=ManualProfileAction(
        kind="profile_action", action="add_rule", columns=["BEST_FCF"], reason="A free cash flow should not be negative."))
    assert db_session.scalar(select(func.count()).select_from(ProposalLogEntry)) == before
    assert (made.origin, made.author, made.proposal_log_id, made.payload["applied_by"]) == (
        "manual", "dev-operator", None, "manual")
    assert made.payload["drift"]["load_id"]
    assert made.id in {a.proposal_id for a in drift_of_feed(db_session, w.feed_id).actions}
    with pytest.raises(Exception, match="no column"):
        add_drift_action(db_session, feed_id=w.feed_id, user=OPERATOR, item=ManualProfileAction(
            kind="profile_action", action="add_rule", columns=["nope"], reason="x"))


def test_a_proposal_made_by_rules_has_neither_a_logged_call_nor_an_author_and_is_only_a_profile_action(db_session, glossary):
    import json

    from sqlalchemy import text

    from tests.configured import a_registered_feed, logged_call
    from tests.test_db_onboarding_guards import refused

    feed = a_registered_feed(db_session, "syn_rule_made")
    insert = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id, origin, "
              "author) VALUES (:log, :kind, CAST(:p AS jsonb), 'A1', ARRAY['backend_team'], :feed, 'rule', :author)")
    ok = {"log": None, "kind": "profile_action", "p": json.dumps({"action": "map_new_column"}), "feed": feed, "author": None}
    db_session.execute(text(insert), ok)
    refused(db_session, "origin_matches_source", insert, **{**ok, "log": logged_call(db_session, "ProfilerAgent", "profiler").id})
    refused(db_session, "origin_matches_source", insert, **{**ok, "author": "dev-operator"})
    # manual_kinds covers every origin but agent (the kinds a person adds), so it refuses this one first
    refused(db_session, "manual_kinds|rule_made_kinds", insert, **{**ok, "kind": "review_summary", "p": json.dumps({"summary": "s"})})


def test_version_two_differs_from_version_one_by_the_new_column(db_session, glossary, tmp_path):
    """The diff of two generated versions names the files that changed (pure: no second go-live is needed here)."""
    v1 = {"curated.sql": "SELECT a FROM raw;\n", "README.md": "a\n"}
    v2 = {"curated.sql": "SELECT a, BEST_FCF FROM raw;\n", "README.md": "a\nBEST_FCF\n"}
    assert [(d.path, d.change) for d in diff_versions(v1, v2)] == [("README.md", "changed"), ("curated.sql", "changed")]
