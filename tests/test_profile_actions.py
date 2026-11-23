"""Phase 7.12, T1: the profile step in a business user's words, and the actions the ProfilerAgent proposes.

Code finds the facts (incomplete records, fit to the glossary); the agent only words them and proposes ACTIONS, each a
`profile_action` proposal on the same logged call as the summary, marked "agent applies" or "I'll do it". A selected
(approved) action reaches the later agents' inputs; a manual one is a to-do on the feed. A person may add an action
(T7): a manual proposal, no model call.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from core.domain.manual import ManualProfileAction
from core.domain.mapping import GlossaryBuilderInput, MappingInput
from core.domain.profiling import FileColumn, GlossaryFit, IncompleteRecords
from core.domain.rules import RuleRecommenderInput
from core.domain.transformation import TransformationInput
from core.proposal_log import ProposalLogEntry
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.upload_sources import UploadSourceRepository
from core.steps.feed_page import feed_page
from core.steps.onboarding import add_item, timeline
from core.steps.profiling import incomplete_records_found
from core.steps.s02_profile import decide_profile_action, fit_to_glossary, selected_actions
from core.steps.s09_rules import rules_input
from ports.config import Settings
from ports.connectors.catalogue import SourceCatalogue
from tests.test_onboarding import OPERATOR, approve, make_world, run_to, view, work


def catalogue_of(w) -> SourceCatalogue:
    return SourceCatalogue.load(w.settings.source_catalogue, w.settings.allowed_source_hosts()).with_uploads(
        UploadSourceRepository(w.session).entries())


def test_the_mock_proposes_placeholders_to_null_for_an_agent_to_apply():
    from ports.llm.mock_rules import _profile_actions

    [a] = _profile_actions([{"kind": "placeholders", "columns": ["REGION"], "count": 3, "detail": "3 of 9 rows hold a placeholder."}], [])
    assert (a["action"], a["applied_by"], a["columns"]) == ("placeholder_to_null", "agent", ["REGION"])


def column(name: str, type_: str = "VARCHAR") -> FileColumn:
    return FileColumn(name=name, type=type_, null_count=0, null_rate=0.0, distinct_count=3)


def test_incomplete_records_are_the_rows_with_a_null_in_a_required_column(tmp_path):
    path = tmp_path / "f.csv"
    path.write_text("id,px,note\n1,10,a\n2,,b\n3,12,\n4,,\n", encoding="utf-8")
    found = incomplete_records_found(path, ["id", "px"])
    assert found == IncompleteRecords(incomplete=2, total=4, required_columns=["id", "px"])
    assert incomplete_records_found(path, ["note"]).incomplete == 2
    assert incomplete_records_found(path, []) is None  # no required column is known: nothing to say


def test_glossary_fit_sorts_columns_into_matched_new_and_unmatched(db_session, glossary):
    elements = GlossaryRepository(db_session).list_current()
    exact = elements[0]
    fit = fit_to_glossary([column(exact.key), column("zzqx_nothing_like_it")], elements)
    assert exact.key in fit.matched and "zzqx_nothing_like_it" in fit.new
    assert set(fit.matched) | set(fit.new) | set(fit.unmatched) == {exact.key, "zzqx_nothing_like_it"}
    assert not set(fit.matched) & set(fit.unmatched)


def test_the_profile_step_shows_business_facts_and_actions_on_one_logged_call(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    work(w)
    items = view(w).items
    [summary] = [i for i in items if i.kind == "profile_summary"]
    actions = [i for i in items if i.kind == "profile_action"]
    p = summary.payload
    fit = GlossaryFit(**p["glossary_fit"])
    assert len(fit.matched) + len(fit.new) + len(fit.unmatched) == len(view(w).inputs.columns)
    assert p["incomplete_records"] is None or 0 <= p["incomplete_records"]["incomplete"] <= p["incomplete_records"]["total"]
    assert actions, "the security master has placeholders and dates held as text: the agent proposes actions"
    kinds = {a.payload["action"] for a in actions}
    assert {"parse_date", "exclude_column"} <= kinds  # dates held as text; a column with no real value
    assert {a.payload["applied_by"] for a in actions} <= {"agent", "manual"}
    assert all(a.reason and a.pre_accepted and a.origin == "agent" for a in actions)
    logs = {ProposalRepository(db_session).get(i.proposal_id).proposal_log_id for i in items}
    assert len(logs) == 1  # the summary and its actions are one model call


def test_a_selected_action_reaches_the_later_agents_and_a_rejected_one_does_not(db_session, glossary, tmp_path):
    for model in (GlossaryBuilderInput, MappingInput, TransformationInput, RuleRecommenderInput):
        assert "profile_actions" in model.model_fields, model
    w = make_world(db_session, tmp_path)
    work(w)
    actions = [i for i in view(w).items if i.kind == "profile_action"]
    refused, kept = actions[0], actions[1:]
    decide_profile_action(db_session, proposal_id=refused.proposal_id, user=OPERATOR, decision="rejected",
                          note="Not for this feed.")
    approve(w)
    ids = {a["proposal_id"] for a in selected_actions(db_session, w.feed_id)}
    assert str(refused.proposal_id) not in ids and {str(a.proposal_id) for a in kept} <= ids
    run_to(w, "rules")
    got = rules_input(db_session, feed_id=w.feed_id, catalogue=catalogue_of(w))
    assert {a["proposal_id"] for a in got.profile_actions} == ids


def test_manual_actions_are_a_to_do_list_on_the_feed(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    work(w)
    manual = [i for i in view(w).items if i.kind == "profile_action" and i.payload["applied_by"] == "manual"]
    approve(w)
    page = feed_page(db_session, FeedRepository_get(db_session, w.feed_id), catalogue_of(w))
    assert {t.proposal_id for t in page.todo_actions} == {m.proposal_id for m in manual}


def FeedRepository_get(session, feed_id):
    from core.repositories.feeds import FeedRepository

    return FeedRepository(session).get(feed_id)


def test_a_person_adds_an_action_and_no_model_is_called(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    work(w)
    column_name = view(w).inputs.columns[0]
    before = db_session.scalar(select(func.count()).select_from(ProposalLogEntry))
    made = add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualProfileAction(
        kind="profile_action", action="exclude_column", columns=[column_name], reason="A scratch column."))
    assert db_session.scalar(select(func.count()).select_from(ProposalLogEntry)) == before
    assert (made.origin, made.author, made.proposal_log_id, made.kind) == ("manual", "dev-operator", None, "profile_action")
    assert made.payload["applied_by"] == "manual" and made.payload["action"] == "exclude_column"
    assert any(t.title == f"profile_action added by {OPERATOR.display_name}"
               for t in timeline(db_session, session_id=w.session_id))
    with pytest.raises(Exception, match="no column"):
        add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualProfileAction(
            kind="profile_action", action="exclude_column", columns=["not_a_column"], reason="x"))


def test_settings_default_keeps_samples_off():  # the facts above never needed a sample value
    assert Settings(_env_file=None).send_sample_values is False


# ---------------------------------------------------------------- selected actions change the later proposals (7.12 A)


def actions_of(w, kind, applied_by=None):
    return [i for i in view(w).items if i.kind == "profile_action" and i.payload["action"] == kind
            and (applied_by is None or i.payload["applied_by"] == applied_by)]


def test_an_approved_cleaning_action_gets_its_transformation_and_a_rejected_one_does_not(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    work(w)
    placeholders = actions_of(w, "placeholder_to_null", "agent")
    assert len(placeholders) >= 2
    refused, kept = placeholders[0], placeholders[1]
    decide_profile_action(db_session, proposal_id=refused.proposal_id, user=OPERATOR, decision="rejected", note="Keep them.")
    dates = actions_of(w, "parse_date")
    cleaned = {p.payload["columns"][0] for p in placeholders}
    for leave in actions_of(w, "exclude_column"):  # these columns stay mapped, so a transformation is possible
        if leave.payload["columns"][0] in cleaned:
            decide_profile_action(db_session, proposal_id=leave.proposal_id, user=OPERATOR, decision="rejected", note="Keep.")
    approve(w)
    run_to(w, "transformations")
    steps = {i.column: [s["op"] for s in i.payload["steps"]] for i in view(w).items if i.kind == "transformation"}
    assert "placeholder_to_null" in steps[kept.payload["columns"][0]]  # approved: the agent proposes it
    assert refused.payload["columns"][0] not in steps  # rejected: no transformation for that column
    assert dates and "parse_date" in steps[dates[0].payload["columns"][0]]


def test_a_column_left_out_by_an_approved_action_is_not_proposed_to_later_agents_and_a_rejected_one_is(
        db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    work(w)
    for name in view(w).inputs.columns[:2]:  # two people-made "leave it out" actions (the mock proposes one per empty column)
        add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualProfileAction(
            kind="profile_action", action="exclude_column", columns=[name], reason="Not needed."))
    leave = [i for i in actions_of(w, "exclude_column", "manual") if i.origin == "manual"]
    assert len(leave) == 2
    refused, kept = leave[0], leave[1]
    decide_profile_action(db_session, proposal_id=refused.proposal_id, user=OPERATOR, decision="rejected", note="Keep it.")
    approve(w)
    run_to(w, "mapping")
    from core.steps.s02_profile import excluded_by_actions

    left = excluded_by_actions(db_session, w.feed_id)
    assert kept.payload["columns"][0] in left and refused.payload["columns"][0] not in left
    asked = {i.column for i in view(w).items}
    assert kept.payload["columns"][0] not in asked
    from core.steps.s06_map import excluded_columns

    assert kept.payload["columns"][0] in excluded_columns(db_session, w.feed_id)
    assert refused.payload["columns"][0] not in excluded_columns(db_session, w.feed_id)  # rejected: not left out
