"""LLM port, local adapter: MockProvider returns schema-valid, deterministic outputs from fixtures."""

import json

import pytest
from pydantic import BaseModel, ValidationError

from ports.llm.base import LLMResult, Message
from ports.llm.mock import MockFixtureMissing, MockInputMissing, MockProvider


class SmokeOutput(BaseModel):
    summary: str
    confidence: float


class NeedsAnotherField(BaseModel):
    field_the_fixture_does_not_have: int


MESSAGES = [Message(role="system", content="You are a test."), Message(role="user", content="Hello")]


def _complete(provider, schema=SmokeOutput, prompt_id="phase0_smoke", prompt_version="v1", messages=MESSAGES):
    return provider.complete(prompt_id=prompt_id, prompt_version=prompt_version,
                             messages=messages, response_schema=schema)


def test_output_validates_against_response_schema():
    result = _complete(MockProvider())
    assert isinstance(result, LLMResult)
    assert isinstance(result.output, SmokeOutput)
    assert result.output.summary
    assert 0.0 <= result.output.confidence <= 1.0


def test_metadata_fields():
    result = _complete(MockProvider())
    assert result.provider == "mock"
    assert result.model == "mock"
    assert result.model_version == "1"
    assert result.prompt_id == "phase0_smoke"
    assert result.prompt_version == "v1"
    assert result.latency_ms == 0
    assert result.raw_ref is None


def test_deterministic_across_calls_and_messages():
    provider = MockProvider()
    first = _complete(provider)
    second = _complete(provider, messages=[Message(role="user", content="Something else")])
    assert first == second
    assert first == _complete(MockProvider())


def test_missing_fixture_names_expected_path(tmp_path):
    with pytest.raises(MockFixtureMissing) as excinfo:
        _complete(MockProvider(fixtures_root=tmp_path), prompt_id="no_such_prompt")
    assert str(tmp_path / "no_such_prompt" / "v1.json") in str(excinfo.value)


def test_fixture_not_matching_schema_raises_validation_error():
    with pytest.raises(ValidationError):
        _complete(MockProvider(), schema=NeedsAnotherField)


@pytest.mark.parametrize("bad", ["../phase0_smoke", "a/b", "a\\b", "", "UPPER"])
def test_prompt_id_and_version_cannot_leave_fixture_folder(bad):
    with pytest.raises(ValueError):
        _complete(MockProvider(), prompt_id=bad)
    with pytest.raises(ValueError):
        _complete(MockProvider(), prompt_version=bad)


def test_result_serialises_the_real_output_fields():
    dumped = json.loads(_complete(MockProvider()).model_dump_json())
    assert set(dumped["output"]) == {"summary", "confidence"}


# ---------------------------------------------------------------- phase 3b: rules (assumption 25)


class Suggestions(BaseModel):
    suggestions: list[dict]


def _assist_messages(term):
    data = {"query": term, "unmatched_terms": [term],
            "glossary": [{"key": "close_price", "name": "Close price", "aliases": ["closing_price"]}]}
    return [Message(role="system", content="s"),
            Message(role="user", content="Suggest.\n\n```json\n" + json.dumps(data) + "\n```\n")]


def test_a_prompt_with_a_rule_is_answered_from_the_input_block(tmp_path):
    provider = MockProvider(fixtures_root=tmp_path)  # no fixture exists: the rule answers
    result = _complete(provider, schema=Suggestions, prompt_id="discovery_assistant",
                       messages=_assist_messages("closing prices"))
    assert [s["element_key"] for s in result.output.suggestions] == ["close_price"]
    assert (result.provider, result.prompt_id, result.prompt_version) == ("mock", "discovery_assistant", "v1")


def test_a_rule_depends_on_the_input_and_on_nothing_else():
    provider = MockProvider()
    first = _complete(provider, schema=Suggestions, prompt_id="discovery_assistant",
                      messages=_assist_messages("closing prices"))
    assert first == _complete(MockProvider(), schema=Suggestions, prompt_id="discovery_assistant",
                              messages=_assist_messages("closing prices"))
    other = _complete(provider, schema=Suggestions, prompt_id="discovery_assistant",
                      messages=_assist_messages("weather"))
    assert other.output.suggestions == []


def test_a_rule_prompt_without_an_input_block_is_an_error():
    with pytest.raises(MockInputMissing):
        _complete(MockProvider(), schema=Suggestions, prompt_id="discovery_assistant", messages=MESSAGES)


def test_a_rule_output_is_validated_against_the_response_schema():
    with pytest.raises(ValidationError):
        _complete(MockProvider(), schema=NeedsAnotherField, prompt_id="discovery_assistant",
                  messages=_assist_messages("closing prices"))
