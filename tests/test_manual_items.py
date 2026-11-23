"""Phase 7.11, T7: a person adds, edits and rejects items at every onboarding stage that has agent items. A manual
item is a proposal like any other (rule 1): origin `manual`, an author, pending until approved, and no model call, so
no proposal_log row (migration 0028). The world is the security master of tests/test_onboarding.py, one operator, with
four-eyes off, so the author's acceptance counts as an edit's does.

Stages without a manual item here: profile (the T1 action list) and drift (T4) do not exist in this repository yet;
generated code (T3) is never hand-edited by design.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from core.domain.glossary import GlossaryElementDraft
from core.domain.manual import ManualGlossary, ManualMapping, ManualNote, ManualRule, ManualTransformation
from core.domain.mapping import ElementOption
from core.proposal_log import ProposalLogEntry
from core.repositories.configuration import MappingRepository, RuleRepository
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.errors import (
    ColumnAlreadyMapped,
    ItemAlreadyPending,
    ManualItemRefused,
    ManualRuleRefused,
    ProposalEditRefused,
    SourceFieldNotMapped,
    StepNotEnterable,
)
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.transformations import TransformationRepository
from core.steps.onboarding import (
    add_item,
    checked_rule_payload,
    edit_item,
    manual_rule_input,
    timeline,
)
from core.steps.s05_glossary import decide_element
from core.steps.s06_map import ColumnExcluded, Mapped, decide_mapping, excluded_columns, mapping_input
from core.steps.s09_rules import RuleStored, decide_rule
from core.steps.s10_transform import TransformationStored, decide_transformation
from ports.notify.inapp import InAppNotifier
from tests.test_onboarding import OPERATOR, make_world, run_to, view

PERSON = "dev-operator"


def log_rows(w) -> int:
    return w.session.scalar(select(func.count()).select_from(ProposalLogEntry))


def pending(w, kind: str):
    return [i for i in view(w).items if i.kind == kind and i.status == "pending"]


def item_of(w, proposal_id):
    return next(i for i in view(w).items if i.proposal_id == proposal_id)


def titles(w, step: str) -> list[str]:
    return [t.title for t in timeline(w.session, session_id=w.session_id) if t.step == step]


def assert_manual(w, made, kind: str) -> None:
    """What every manual item is, the same at every stage."""
    assert (made.origin, made.author, made.proposal_log_id, made.status, made.kind) == (
        "manual", PERSON, None, "pending", kind)
    shown = item_of(w, made.id)
    assert (shown.origin, shown.author, shown.pre_accepted, shown.needs_decision) == ("manual", PERSON, True, False)
    assert shown.reason and shown.reason.strip()
    assert any(f"{kind} added by {OPERATOR.display_name}" == t for t in titles(w, shown_step(kind)))


def shown_step(kind: str) -> str:
    return {"glossary_element": "glossary", "mapping": "mapping", "transformation": "transformations",
            "rule": "rules", "review_summary": "go_live"}[kind]


# ---------------------------------------------------------------- glossary


def test_glossary_add_edit_reject(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "glossary")
    column = pending(w, "glossary_element")[0].column
    agents_item = pending(w, "glossary_element")[0]
    decide_element(db_session, proposal_id=agents_item.proposal_id, user=OPERATOR, decision="rejected", choice=None,
                   note="I will write it myself", notifier=InAppNotifier(db_session), stewards=[])
    before = log_rows(w)

    draft = GlossaryElementDraft(key="manual_probe_element", name="Manual probe", domain="reference_data",
                                 data_type="string", definition="Made by hand for the test.")
    item = ManualGlossary(kind="glossary_element", source_field=column, reason="The agent's name is not ours.",
                          option=ElementOption(kind="new_element", key="manual_probe_element", element=draft))
    made = add_item(db_session, session_id=w.session_id, item=item, user=OPERATOR)
    assert log_rows(w) == before  # no model call, no fake log row
    assert_manual(w, made, "glossary_element")
    assert made.payload["run_id"] is None and made.payload["recommended"] == 0

    with pytest.raises(ItemAlreadyPending):  # one pending item per column
        add_item(db_session, session_id=w.session_id, item=item, user=OPERATOR)


def test_glossary_manual_item_edit_and_reject(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "glossary")
    agents_item = pending(w, "glossary_element")[0]
    decide_element(db_session, proposal_id=agents_item.proposal_id, user=OPERATOR, decision="rejected", choice=None,
                   note="mine", notifier=InAppNotifier(db_session), stewards=[])
    draft = GlossaryElementDraft(key="manual_probe_element", name="Manual probe", domain="reference_data",
                                 data_type="string", definition="First wording.")
    made = add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualGlossary(
        kind="glossary_element", source_field=agents_item.column, reason="Mine.",
        option=ElementOption(kind="new_element", key="manual_probe_element", element=draft)))

    payload = made.payload | {"options": [made.payload["options"][0] | {
        "element": made.payload["options"][0]["element"] | {"definition": "Second wording."}}]}
    edited = edit_item(db_session, session_id=w.session_id, proposal_id=made.id, payload=payload, user=OPERATOR)
    assert (edited.origin, edited.author, edited.proposal_log_id, edited.edited_by) == ("manual", PERSON, None, PERSON)
    assert item_of(w, edited.id).edited and item_of(w, edited.id).payload["options"][0]["element"]["definition"] == "Second wording."
    assert ProposalRepository(db_session).get(made.id).status == "superseded"

    decided = decide_element(db_session, proposal_id=edited.id, user=OPERATOR, decision="rejected", choice=None,
                             note="Changed my mind", notifier=InAppNotifier(db_session), stewards=[])
    assert decided.decision == "rejected" and ProposalRepository(db_session).get(edited.id).status == "rejected"
    assert not GlossaryRepository(db_session).exists("manual_probe_element")


def test_glossary_manual_alias_and_refusals(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "glossary")
    column = pending(w, "glossary_element")[0].column
    decide_element(db_session, proposal_id=pending(w, "glossary_element")[0].proposal_id, user=OPERATOR,
                   decision="rejected", choice=None, note="mine", notifier=InAppNotifier(db_session), stewards=[])
    taken = GlossaryRepository(db_session).list_current()[0].key
    with pytest.raises(ManualItemRefused, match="already in the glossary"):
        add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualGlossary(
            kind="glossary_element", source_field=column, reason="x", option=ElementOption(
                kind="new_element", key=taken, element=GlossaryElementDraft(
                    key=taken, name="n", domain="reference_data", data_type="string", definition="d"))))
    with pytest.raises(ManualItemRefused, match="not in the glossary"):
        add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualGlossary(
            kind="glossary_element", source_field=column, reason="x",
            option=ElementOption(kind="alias", key="no_such_element", alias="some_alias")))
    with pytest.raises(ManualItemRefused, match="no column"):
        add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualGlossary(
            kind="glossary_element", source_field="not_a_column", reason="x",
            option=ElementOption(kind="alias", key=taken, alias="some_alias")))
    made = add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualGlossary(
        kind="glossary_element", source_field=column, reason="Another name for it.",
        option=ElementOption(kind="alias", key=taken, alias="a_name_from_the_desk")))
    assert made.payload["outcome"] == "duplicate_alias"
    with pytest.raises(StepNotEnterable):  # a mapping item is not added at the glossary step
        add_item(db_session, session_id=w.session_id, user=OPERATOR,
                 item=ManualMapping(kind="mapping", source_field=column, element_key=taken, reason="x"))


# ---------------------------------------------------------------- mapping, including leaving a column out


def a_free_element(w) -> str:
    mapped = {m.element_key for m in MappingRepository(w.session).current_for_feed(w.feed_id)}
    return next(e.key for e in GlossaryRepository(w.session).list_current() if e.key not in mapped)


def test_mapping_to_any_element_edit_and_reject(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "mapping")
    agents_item = pending(w, "mapping")[0]
    decide_mapping(db_session, proposal_id=agents_item.proposal_id, user=OPERATOR, decision="rejected", choice=None,
                   note="wrong element")
    element = a_free_element(w)
    before = log_rows(w)
    made = add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualMapping(
        kind="mapping", source_field=agents_item.column, element_key=element, reason="The desk says so."))
    assert log_rows(w) == before
    assert_manual(w, made, "mapping")
    assert made.payload["candidates"] == [element] and made.payload["recommended"] == element

    edited = edit_item(db_session, session_id=w.session_id, proposal_id=made.id,
                       payload=made.payload | {"reason": "The desk says so, in writing."}, user=OPERATOR)
    assert (edited.origin, edited.author) == ("manual", PERSON)

    result = decide_mapping(db_session, proposal_id=edited.id, user=OPERATOR, decision="approved", choice=None,
                            note=None)
    assert isinstance(result, Mapped) and result.mapping.element_key == element
    assert result.mapping.source_field == agents_item.column

    with pytest.raises(ManualItemRefused, match="not in the glossary"):
        add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualMapping(
            kind="mapping", source_field=agents_item.column, element_key="no_such_element", reason="x"))


def test_a_manual_mapping_can_be_rejected(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "mapping")
    first = pending(w, "mapping")[0]
    decide_mapping(db_session, proposal_id=first.proposal_id, user=OPERATOR, decision="rejected", choice=None, note="n")
    made = add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualMapping(
        kind="mapping", source_field=first.column, element_key=a_free_element(w), reason="Try this."))
    decide_mapping(db_session, proposal_id=made.id, user=OPERATOR, decision="rejected", choice=None, note="No.")
    assert ProposalRepository(db_session).get(made.id).status == "rejected"
    assert all(m.source_field != first.column for m in MappingRepository(db_session).current_for_feed(w.feed_id))


def test_a_column_can_be_left_out_and_the_agent_does_not_ask_again(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "mapping")
    first = pending(w, "mapping")[0]
    decide_mapping(db_session, proposal_id=first.proposal_id, user=OPERATOR, decision="rejected", choice=None, note="n")
    made = add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualMapping(
        kind="mapping", source_field=first.column, excluded=True, reason="A scratch column."))
    assert_manual(w, made, "mapping")
    assert item_of(w, made.id).summary.endswith("left out (not loaded)")
    with pytest.raises(ProposalEditRefused, match="nothing to edit"):
        edit_item(db_session, session_id=w.session_id, proposal_id=made.id, payload=made.payload | {"reason": "x"},
                  user=OPERATOR)

    result = decide_mapping(db_session, proposal_id=made.id, user=OPERATOR, decision="approved", choice=None, note=None)
    assert isinstance(result, ColumnExcluded) and result.source_field == first.column
    assert all(m.source_field != first.column for m in MappingRepository(db_session).current_for_feed(w.feed_id))
    assert first.column in excluded_columns(db_session, w.feed_id)
    try:
        asked = [c.name for c in mapping_input(db_session, feed_id=w.feed_id).columns]
    except Exception:  # NothingToMap: every column is mapped or left out
        asked = []
    assert first.column not in asked


def test_only_an_unmapped_column_is_left_out(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "mapping")
    mapped = MappingRepository(db_session).current_for_feed(w.feed_id)
    if not mapped:  # the step's items are pending: map one by hand first
        first = pending(w, "mapping")[0]
        decide_mapping(db_session, proposal_id=first.proposal_id, user=OPERATOR, decision="approved", choice=None, note=None)
        mapped = MappingRepository(db_session).current_for_feed(w.feed_id)
    with pytest.raises(ColumnAlreadyMapped):
        add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualMapping(
            kind="mapping", source_field=mapped[0].source_field, excluded=True, reason="x"))


# ---------------------------------------------------------------- transformations


def a_mapped_column(w) -> str:
    return MappingRepository(w.session).current_for_feed(w.feed_id)[0].source_field


def test_transformation_add_edit_reject(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "transformations")
    column = a_mapped_column(w)
    for existing in pending(w, "transformation"):  # free every column for the test
        if existing.column == column:
            decide_transformation(db_session, proposal_id=existing.proposal_id, user=OPERATOR, decision="rejected",
                                  note="mine")
    before = log_rows(w)
    made = add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualTransformation(
        kind="transformation", source_field=column, steps=[{"op": "trim"}], reason="Stray spaces in the file."))
    assert log_rows(w) == before
    assert_manual(w, made, "transformation")
    assert made.payload["steps"] == [{"op": "trim"}] and made.payload["confidence"] == 1.0

    with pytest.raises(ItemAlreadyPending):
        add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualTransformation(
            kind="transformation", source_field=column, steps=[{"op": "trim"}], reason="again"))

    edited = edit_item(db_session, session_id=w.session_id, proposal_id=made.id,
                       payload=made.payload | {"steps": [{"op": "trim"}, {"op": "cast", "type": "string"}]}, user=OPERATOR)
    assert (edited.origin, edited.author) == ("manual", PERSON)
    stored = decide_transformation(db_session, proposal_id=edited.id, user=OPERATOR, decision="approved")
    assert isinstance(stored, TransformationStored)
    stored_steps = [t for t in TransformationRepository(db_session).current_for_feed(w.feed_id)
                    if t.source_field == column][0].steps
    assert [s.op for s in stored_steps] == ["trim", "cast"]


def test_a_manual_transformation_can_be_rejected_and_needs_a_mapped_column(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "transformations")
    in_file = [c.name for c in FeedProfileRepository(db_session).latest(w.feed_id).columns]
    mapped = {m.source_field for m in MappingRepository(db_session).current_for_feed(w.feed_id)}
    unmapped = next((c for c in in_file if c not in mapped), None)
    if unmapped is not None:  # a column the file has but nothing maps: nothing to clean into
        with pytest.raises(SourceFieldNotMapped):
            add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualTransformation(
                kind="transformation", source_field=unmapped, steps=[{"op": "trim"}], reason="x"))
    with pytest.raises(ManualItemRefused, match="no column"):  # the file has no such column at all
        add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualTransformation(
            kind="transformation", source_field="not_a_column", steps=[{"op": "trim"}], reason="x"))
    column = a_mapped_column(w)
    for existing in pending(w, "transformation"):
        if existing.column == column:
            decide_transformation(db_session, proposal_id=existing.proposal_id, user=OPERATOR, decision="rejected", note="m")
    made = add_item(db_session, session_id=w.session_id, user=OPERATOR, item=ManualTransformation(
        kind="transformation", source_field=column, steps=[{"op": "trim"}], reason="Try."))
    decide_transformation(db_session, proposal_id=made.id, user=OPERATOR, decision="rejected", note="No.")
    assert ProposalRepository(db_session).get(made.id).status == "rejected"


# ---------------------------------------------------------------- rules


SAFE_SQL = "SELECT _row FROM curated WHERE 1 = 0"


def a_mapped_element(w) -> str:
    return MappingRepository(w.session).current_for_feed(w.feed_id)[0].element_key


def rule(w, key="manual_probe_rule", sql=SAFE_SQL, description="Nothing is ever flagged.", elements=None):
    return ManualRule(kind="rule", rule_key=key, sql=sql, description=description,
                      element_keys=elements or [a_mapped_element(w)])


def add_rule(w, item):
    agent_input, proposed, own = manual_rule_input(w.session, session_id=w.session_id, item=item, ctx=w.ctx)
    payload = checked_rule_payload(agent_input, proposed, own, ctx=w.ctx)
    return add_item(w.session, session_id=w.session_id, item=item, user=OPERATOR, rule_payload=payload)


def test_a_manual_rule_is_back_tested_then_added_and_approved(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "rules")
    before = log_rows(w)
    made = add_rule(w, rule(w))
    assert log_rows(w) == before
    assert_manual(w, made, "rule")
    assert made.payload["backtest"]["status"] in ("ran", "not_back_tested")
    assert made.required_approver_roles == ["data_steward", "checker"]

    with pytest.raises(ItemAlreadyPending):
        add_rule(w, rule(w))

    result = decide_rule(db_session, proposal_id=made.id, user=OPERATOR, decision="approved")
    assert isinstance(result, RuleStored)
    assert any(r.rule_key == "manual_probe_rule" for r in RuleRepository(db_session).current_for_feed(w.feed_id))


def test_a_manual_rule_from_a_template(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "rules")
    agent_input, _, _ = manual_rule_input(db_session, session_id=w.session_id, ctx=w.ctx,
                                          item=rule(w, key="probe_for_offers"))
    taken = set(agent_input.existing_keys) | set(agent_input.rejected_keys)
    offers = [o for e in agent_input.elements for o in e.templates if o.rule_key not in taken]
    if not offers:
        pytest.skip("the agent already proposed every template of this feed")
    made = None
    for offer in offers:  # a template that flags most of this file is refused like any rule: take the first that passes
        try:
            made = add_rule(w, ManualRule(kind="rule", template=offer.rule_key))
            break
        except (ManualRuleRefused, ItemAlreadyPending):  # (the input's key list is cut at MAX_KNOWN_KEYS)
            continue
    if made is not None:
        assert made.payload["rule_key"] == offer.rule_key and made.payload["template"] == offer.template
        return
    # every unused template flags most of this file: the rule is still taken whole from the template, and refused
    for offer in offers:
        try:
            _, proposed, _ = manual_rule_input(db_session, session_id=w.session_id, ctx=w.ctx,
                                               item=ManualRule(kind="rule", template=offer.rule_key))
        except ItemAlreadyPending:
            continue
        assert (proposed.rule_key, proposed.sql, proposed.description, proposed.template) == (
            offer.rule_key, offer.sql, offer.description, offer.template)
        return
    pytest.fail("no template of this feed could be tried")
    with pytest.raises(ManualItemRefused, match="No template"):
        manual_rule_input(db_session, session_id=w.session_id, ctx=w.ctx, item=ManualRule(kind="rule", template="nope"))


def test_the_rules_step_lists_the_templates_a_person_may_pick(db_session, glossary, tmp_path):
    from core.steps.onboarding import workspace

    w = make_world(db_session, tmp_path)
    run_to(w, "rules")
    offered = workspace(db_session, session_id=w.session_id, settings=w.settings).inputs.rule_templates
    taken = {i.payload["rule_key"] for i in view(w).items if i.kind == "rule"}
    assert all({"template", "rule_key", "description", "sql", "element_keys"} <= set(o) for o in offered)
    assert not {o["rule_key"] for o in offered} & taken  # one the agent already proposed is not offered again
    for offer in offered[:1]:  # a listed template is accepted as the person's pick
        _, proposed, _ = manual_rule_input(db_session, session_id=w.session_id, ctx=w.ctx,
                                           item=ManualRule(kind="rule", template=offer["rule_key"]))
        assert proposed.sql == offer["sql"]
    other_step = workspace(db_session, session_id=w.session_id, settings=w.settings, step="mapping").inputs
    assert other_step.rule_templates == []


@pytest.mark.parametrize("sql, why", [
    ("DROP TABLE curated", "plain SELECT|SELECT"),
    ("SELECT 1 AS not_a_row", "_row"),
    ("SELECT _row FROM curated", "more than half"),
    ("SELECT _row FROM no_such_table", ""),
])
def test_a_manual_rule_that_fails_its_back_test_is_refused_and_nothing_is_written(db_session, glossary, tmp_path, sql, why):
    w = make_world(db_session, tmp_path)
    run_to(w, "rules")
    count = len(ProposalRepository(db_session).list_for_feed(w.feed_id, "rule"))
    with pytest.raises(ManualRuleRefused, match=why or ".+"):
        add_rule(w, rule(w, key="bad_probe_rule", sql=sql))
    assert len(ProposalRepository(db_session).list_for_feed(w.feed_id, "rule")) == count


def test_a_manual_rule_naming_an_element_the_feed_does_not_map_is_refused(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "rules")
    with pytest.raises(ManualRuleRefused, match="does not map"):
        add_rule(w, rule(w, elements=["no_such_element"]))


def test_an_agents_rule_is_edited_again_back_tested_and_one_is_rejected(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "rules", flagged_rules="approved")
    rules = pending(w, "rule")
    assert rules, "the security master's rules step has pending agent rules"
    target = rules[0]
    edited_payload = target.payload | {"description": "Reworded by the steward."}
    agent_input, proposed, own = manual_rule_input(db_session, session_id=w.session_id, ctx=w.ctx, edit_of=target.proposal_id,
                                                   edited=edited_payload, item=None)
    rechecked = checked_rule_payload(agent_input, proposed, own, ctx=w.ctx)
    edited = edit_item(db_session, session_id=w.session_id, proposal_id=target.proposal_id, payload=rechecked, user=OPERATOR)
    assert (edited.origin, edited.edited_by) == ("agent", PERSON) and edited.proposal_log_id is not None
    assert item_of(w, edited.id).payload["description"] == "Reworded by the steward."

    broken = target.payload | {"sql": "DROP TABLE curated"}
    agent_input, proposed, own = manual_rule_input(db_session, session_id=w.session_id, ctx=w.ctx, edit_of=edited.id,
                                                   edited=broken, item=None)
    with pytest.raises(ManualRuleRefused):
        checked_rule_payload(agent_input, proposed, own, ctx=w.ctx)

    others = [i for i in pending(w, "rule") if i.proposal_id != edited.id]
    if others:
        decide_rule(db_session, proposal_id=others[0].proposal_id, user=OPERATOR, decision="rejected", note="Wrong here.")
        assert ProposalRepository(db_session).get(others[0].proposal_id).status == "rejected"


# ---------------------------------------------------------------- go-live: a note on the review summary


def test_a_note_on_the_go_live_summary_is_an_edit_that_keeps_the_summary(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "go_live")
    summary = pending(w, "review_summary")[0]
    before = log_rows(w)
    first = add_item(db_session, session_id=w.session_id, user=OPERATOR,
                     item=ManualNote(kind="review_summary", note="Desk head has seen the test load."))
    second = add_item(db_session, session_id=w.session_id, user=OPERATOR,
                      item=ManualNote(kind="review_summary", note="Re-check the volume column in a week."))
    assert log_rows(w) == before
    assert (first.origin, second.origin) == ("agent", "agent") and second.edited_from == first.id
    assert [n["text"] for n in second.payload["notes"]] == ["Desk head has seen the test load.",
                                                            "Re-check the volume column in a week."]
    assert {k: v for k, v in second.payload.items() if k != "notes"} == {
        k: v for k, v in summary.payload.items() if k != "notes"}
    shown = item_of(w, second.id)
    assert shown.edited and shown.edited_by == PERSON and shown.pre_accepted
    assert titles(w, "go_live").count("review_summary edited") == 2
