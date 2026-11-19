"""Phase 7.8, task 4.1: the fixed safe set of transform steps (plan assumption 27) and the key policy (29).

Nothing outside the set is ever accepted: not by these models, and (test_db_transformation_guards.py) not by
the database CHECK on the JSON either.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import TypeAdapter, ValidationError

from core.domain.transformation import KeyPolicyDraft, TransformationDraft, TransformStep

STEP = TypeAdapter(TransformStep)
FEED = uuid.uuid4()


@pytest.mark.parametrize("step", [
    {"op": "trim"},
    {"op": "placeholder_to_null", "values": ["N/A"]},
    {"op": "placeholder_to_null", "values": [str(n) for n in range(10)]},
    {"op": "placeholder_to_null", "values": ["x" * 16]},
    {"op": "parse_date", "format": "DD-MM-YYYY"},
    {"op": "cast", "type": "decimal"},
    {"op": "rename", "to": "price_date"},
])
def test_the_safe_set_is_accepted(step):
    assert STEP.validate_python(step).op == step["op"]


@pytest.mark.parametrize("step", [
    {"op": "upper"}, {"op": "sql", "text": "SELECT 1"}, {}, {"op": None}, {"op": "trim", "extra": 1},
    {"op": "placeholder_to_null"}, {"op": "placeholder_to_null", "values": []},
    {"op": "placeholder_to_null", "values": [str(n) for n in range(11)]},
    {"op": "placeholder_to_null", "values": ["x" * 17]}, {"op": "placeholder_to_null", "values": [5]},
    {"op": "placeholder_to_null", "values": "NA"},
    {"op": "parse_date"}, {"op": "parse_date", "format": "YYYY/MM/DD"}, {"op": "parse_date", "format": "dd-mm-yyyy"},
    {"op": "cast"}, {"op": "cast", "type": "float"}, {"op": "cast", "type": "string", "x": 1},
    {"op": "rename"}, {"op": "rename", "to": "Price"}, {"op": "rename", "to": "1price"}, {"op": "rename", "to": "a-b"},
    {"op": "rename", "to": "a" * 64}, {"op": "rename", "to": ""},
])
def test_anything_outside_the_set_is_refused(step):
    with pytest.raises(ValidationError):
        STEP.validate_python(step)


def draft(steps):
    return TransformationDraft(feed_id=FEED, source_field="PX", steps=steps)


def test_a_draft_keeps_the_order_and_dumps_json():
    d = draft([{"op": "trim"}, {"op": "cast", "type": "integer"}, {"op": "rename", "to": "px"}])
    assert [s.op for s in d.steps] == ["trim", "cast", "rename"]
    assert d.steps_json() == [{"op": "trim"}, {"op": "cast", "type": "integer"}, {"op": "rename", "to": "px"}]


def test_rename_may_be_last():
    draft([{"op": "trim"}, {"op": "rename", "to": "px"}])


@pytest.mark.parametrize("steps", [
    [],
    [{"op": "trim"}, {"op": "trim"}],  # each op at most once
    [{"op": "rename", "to": "px"}, {"op": "trim"}],  # rename last
    [{"op": "trim"}] * 7,
    [{"op": "upper"}],
])
def test_a_draft_refuses(steps):
    with pytest.raises(ValidationError):
        draft(steps)


def test_a_draft_names_its_column():
    with pytest.raises(ValidationError):
        TransformationDraft(feed_id=FEED, source_field="  ", steps=[{"op": "trim"}])


def test_a_key_policy_is_one_of_three_with_one_to_five_key_columns():
    for policy in ("hold", "keep_last", "flag"):
        assert KeyPolicyDraft(policy=policy, key_columns=["a"], reason="Because.").policy == policy
    KeyPolicyDraft(policy="flag", key_columns=list("abcde"), reason="Because.")


@pytest.mark.parametrize("change", [
    {"policy": "drop"}, {"key_columns": []}, {"key_columns": list("abcdef")}, {"key_columns": [" "]},
    {"key_columns": [""]}, {"key_columns": ["a", "a"]}, {"reason": " "},
])
def test_a_key_policy_refuses(change):
    fields = {"policy": "flag", "key_columns": ["a"], "reason": "Because.", **change}
    with pytest.raises(ValidationError):
        KeyPolicyDraft(**fields)
