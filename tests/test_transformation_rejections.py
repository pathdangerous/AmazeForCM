"""Phase 7.22, T5: a transformation a person rejected with a reason is not proposed again for the same column and kind.

"Kind" is the step's op (trim, placeholder_to_null, parse_date, cast, rename). The memory is the feed's person-made
rejections only (a rejection by `system`, as Back makes, is no judgement: 7.13 T4). The agent's input carries them; the mock
respects them; and the agent drops any such op from an answer in code, so the guarantee does not rest on the model.
"""

from __future__ import annotations

from core.domain.transformation import RejectedTransform, TransformationInput, TransformationOutput
from core.repositories.proposals import ProposalRepository
from core.repositories.transformations import TransformationRepository
from core.steps.onboarding import back
from core.steps.s10_transform import decide_transformation
from tests.configured import a_registered_feed, approved_mapping, new_element, approved_element, transformation_proposal
from tests.test_onboarding import OPERATOR, approve, current, make_world, run_to, view, work


def rejected_by_a_person(s, feed_id, column, steps, note="Not for this feed."):
    p = transformation_proposal(s, feed_id, column, steps)
    decide_transformation(s, proposal_id=p.id, user=OPERATOR, decision="rejected", note=note)
    return p


def test_a_persons_rejection_with_its_reason_is_in_the_feeds_memory_and_a_system_one_is_not(db_session, glossary):
    feed = a_registered_feed(db_session)
    rejected_by_a_person(db_session, feed, "COUNTRY", [{"op": "placeholder_to_null", "values": ["NA"]}, {"op": "trim"}],
                         note="NA is a real country code here.")
    by_system = transformation_proposal(db_session, feed, "REGION", [{"op": "trim"}])
    ProposalRepository(db_session).record_decision(by_system.id, user=OPERATOR.model_copy(update={"id": "system"}),
                                                   decision="rejected", note="rerun after Back")
    found = TransformationRepository(db_session).rejected_steps(feed)
    assert found == [RejectedTransform(source_field="COUNTRY", ops=["placeholder_to_null", "trim"],
                                       reason="NA is a real country code here.")]


def test_the_agent_input_carries_the_rejections_of_its_columns_only(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "transformations")
    column = next(i.column for i in view(w).items if i.kind == "transformation")
    TransformationRepository  # the input is built from the feed's open columns
    first = next(i for i in view(w).items if i.kind == "transformation" and i.column == column)
    decide_transformation(db_session, proposal_id=first.proposal_id, user=OPERATOR, decision="rejected", note="No.")
    from core.steps.s10_transform import transform_input

    agent_input = transform_input(db_session, feed_id=w.feed_id)
    assert [r.source_field for r in agent_input.rejected] == [column]
    assert agent_input.rejected[0].reason == "No." and agent_input.rejected[0].ops == [s["op"] for s in first.payload["steps"]]


def column_input(**over):
    from core.domain.transformation import ColumnToTransform
    import uuid

    base = dict(source_field="COUNTRY", element_key="country", element_type="string", duckdb_type="VARCHAR", null_count=0,
                row_count=10, placeholder_values=["NA"], placeholder_count=2, text_date_format=None,
                date_format_ambiguous=False, all_empty=False, constant=False)
    return TransformationInput(feed_id=uuid.uuid4(), feed_code="x", columns=[ColumnToTransform(**{**base, **over})])


def test_the_mock_does_not_propose_an_op_a_person_rejected_for_that_column():
    from ports.llm.mock_rules import transformation

    plain = transformation(column_input().model_dump(mode="json"))
    assert [s["op"] for s in plain["columns"][0]["steps"]] == ["placeholder_to_null"]
    told = column_input().model_copy(update={"rejected": [RejectedTransform(
        source_field="COUNTRY", ops=["placeholder_to_null"], reason="NA is a country.")]})
    again = transformation(told.model_dump(mode="json"))
    assert again["columns"][0]["steps"] == []


def test_the_agent_drops_a_rejected_op_from_any_answer_and_a_column_left_with_none_gets_no_proposal():
    from agents.transformation import TransformationAgent

    told = column_input().model_copy(update={"rejected": [RejectedTransform(
        source_field="COUNTRY", ops=["placeholder_to_null"], reason="NA is a country.")]})
    answer = TransformationOutput.model_validate({"columns": [{
        "source_field": "COUNTRY", "reason": "x", "confidence": 0.9,
        "steps": [{"op": "trim"}, {"op": "placeholder_to_null", "values": ["NA"]}]}]})
    [kept] = TransformationAgent.to_proposals(object.__new__(TransformationAgent), told, answer)
    assert [s["op"] for s in kept.payload["steps"]] == ["trim"]
    only = TransformationOutput.model_validate({"columns": [{
        "source_field": "COUNTRY", "reason": "x", "confidence": 0.9,
        "steps": [{"op": "placeholder_to_null", "values": ["NA"]}]}]})
    assert TransformationAgent.to_proposals(object.__new__(TransformationAgent), told, only) == []


def test_after_a_rejection_a_later_run_does_not_propose_the_same_op_for_the_column(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    run_to(w, "transformations")
    proposals = ProposalRepository(db_session)
    target = next(p for p in proposals.list_for_feed(w.feed_id, "transformation", "pending"))
    ops = {s["op"] for s in target.payload["steps"]}
    column = target.payload["source_field"]
    decide_transformation(db_session, proposal_id=target.id, user=OPERATOR, decision="rejected", note="Not wanted.")
    db_session.commit()

    back(db_session, session_id=w.session_id, to_step="transformations", user=OPERATOR)  # the step runs again
    db_session.commit()
    work(w)

    again = [p for p in proposals.list_for_feed(w.feed_id, "transformation") if p.payload["source_field"] == column
             and p.status == "pending"]
    assert all(not ({s["op"] for s in p.payload["steps"]} & ops) for p in again), "the rejected op came back"
    assert current(w).step_states["transformations"] in ("ready", "needs_decision")
