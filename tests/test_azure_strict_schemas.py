"""Azure strict structured outputs accept a narrow JSON Schema. This walks the response_format every registered agent
(and every evaluator) would send, exactly as ports/llm/azure_openai.py builds it, with no network, and names each
agent, field and rule that strict mode would refuse (the live ProfilerAgent call got HTTP 400).

Rules checked (Azure OpenAI structured outputs, strict): the root is an object; every object lists every property in
`required` and has additionalProperties false (so no free-form dict); no keyword outside the supported list (no
minLength/maxLength/pattern/format, minimum/maximum/multipleOf, minItems/maxItems/uniqueItems, default, allOf, not,
oneOf, discriminator, patternProperties, ...); at most 5 levels of nesting and 100 properties in all."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from ports.llm.azure_openai import response_format_for

AGENT_MODULES = ("discovery_assistant", "exception_fix", "glossary_builder", "intent", "mapping", "profiler",
                 "report_summariser", "review_summariser", "rule_recommender", "source_discovery", "table_extractor",
                 "transformation")
SUPPORTED = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "anyOf", "$ref", "$defs",
             "description", "title"}
MAX_DEPTH, MAX_PROPERTIES = 5, 100


def _schemas() -> dict[str, type]:
    for name in AGENT_MODULES:
        importlib.import_module(f"agents.{name}")
    from agents.base import Agent
    from agents.evaluate_agents import EVALUATORS

    found, todo = {}, [*Agent.__subclasses__(), *EVALUATORS.values()]
    while todo:
        cls = todo.pop()
        todo.extend(cls.__subclasses__())
        schema = getattr(cls, "output_schema", None)
        if schema is not None:
            found[schema.__name__] = schema
    return found


def _resolve(node: dict, root: dict) -> dict:
    return root["$defs"][node["$ref"].rsplit("/", 1)[1]] if "$ref" in node else node


def problems(schema: dict[str, Any]) -> list[str]:
    """Every strict-mode refusal in one response_format schema, as 'path: rule'."""
    found: list[str] = []
    count = [0]
    if schema.get("type") != "object":
        found.append("<root>: the root must be an object")

    def walk(node: Any, path: str, depth: int) -> None:
        if not isinstance(node, dict):
            return
        for key in node:
            if key not in SUPPORTED:
                found.append(f"{path}: unsupported keyword {key!r}")
        node = _resolve(node, schema)
        if node.get("type") == "object" or "properties" in node:
            depth += 1  # Azure counts levels of objects
            if depth > MAX_DEPTH:
                found.append(f"{path}: objects nested deeper than {MAX_DEPTH} levels")
                return
            props = node.get("properties", {})
            count[0] += len(props)
            if node.get("additionalProperties") is not False:
                found.append(f"{path}: additionalProperties must be false")
            missing = sorted(set(props) - set(node.get("required", [])))
            if missing:
                found.append(f"{path}: not required: {', '.join(missing)}")
            for name, child in props.items():
                walk(child, f"{path}.{name}", depth)
        for child in node.get("anyOf", []):
            walk(child, f"{path}|", depth)
        if "items" in node:
            walk(node["items"], f"{path}[]", depth)

    walk(schema, "<root>", 0)
    for name, definition in schema.get("$defs", {}).items():
        for key in definition:
            if key not in SUPPORTED:
                found.append(f"$defs.{name}: unsupported keyword {key!r}")
    if count[0] > MAX_PROPERTIES * 2:  # defs are counted once per use here, so only an absurd total is flagged
        found.append(f"<root>: {count[0]} properties")
    return sorted(set(found))


def test_the_checker_catches_each_planted_strict_mode_refusal():
    bad = {"type": "object", "properties": {"a": {"type": "string", "maxLength": 3}, "b": {"type": "object",
           "additionalProperties": {"type": "string"}}, "c": {"type": "integer", "minimum": 0, "default": 1}},
           "required": ["a"], "additionalProperties": False}
    found = " | ".join(problems(bad))
    for expected in ("maxLength", "not required: b, c", "<root>.b: additionalProperties must be false", "minimum", "default"):
        assert expected in found
    assert problems({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
                     "additionalProperties": False}) == []


@pytest.mark.parametrize("name", sorted(_schemas()))
def test_every_agent_schema_is_accepted_by_azure_strict_mode(name):
    fmt = response_format_for(_schemas()[name])
    assert fmt["json_schema"]["strict"] is True
    assert problems(fmt["json_schema"]["schema"]) == [], name


# ---------------------------------------------------------------- the request, the answer and the error

def test_the_request_drops_value_keywords_but_keeps_a_field_that_is_named_like_one():
    from pydantic import BaseModel, Field

    class Odd(BaseModel):
        default: str = Field("x", min_length=1, max_length=9, pattern="^[a-z]+$")
        format: int = Field(3, ge=0, le=9)

    schema = response_format_for(Odd)["json_schema"]["schema"]
    assert set(schema["properties"]) == {"default", "format"}
    assert problems(schema) == []


def test_params_are_asked_for_as_pairs_and_come_back_as_the_same_dict():
    from core.domain.profiling import ProposedAction

    base = {"action": "parse_date", "columns": ["d"], "applied_by": "agent", "reason": "dates"}
    from_pairs = ProposedAction.model_validate({**base, "params": [{"key": "format", "value": "%d/%m/%Y"}]})
    from_dict = ProposedAction.model_validate({**base, "params": {"format": "%d/%m/%Y"}})
    assert from_pairs == from_dict and from_pairs.params == {"format": "%d/%m/%Y"}
    assert ProposedAction.model_validate({**base, "params": []}).params == {}
    assert from_pairs.model_dump(mode="json")["params"] == {"format": "%d/%m/%Y"}  # stored and served as a dict
    asked = ProposedAction.model_json_schema()["properties"]["params"]
    assert asked["type"] == "array" and asked["items"]["required"] == ["key", "value"]


def _refusal(tmp_path, key="k-secret-123"):
    from core.domain.profiling import ProfilerOutput
    from ports.llm.base import LLMUnavailable
    from tests.test_port_llm_azure import ENDPOINT, MESSAGES, make  # the recorded-transport helpers

    body = {"error": {"code": "invalid_json_schema", "param": "response_format",
                      "message": f"Invalid schema for response_format 'ProfilerOutput': 'params' at {ENDPOINT} key {key}",
                      "type": "invalid_request_error"}}
    provider, server, _, _ = make(tmp_path, ("response", 400, body, {}), key=key)
    with pytest.raises(LLMUnavailable) as caught:
        provider.complete(prompt_id="profiler", prompt_version="1", messages=MESSAGES, response_schema=ProfilerOutput)
    return caught.value, server


def test_a_400_names_azures_own_message_and_nothing_secret(tmp_path):
    error, server = _refusal(tmp_path)
    assert error.reason == "provider_error" and len(server.requests) == 1
    assert error.detail.startswith("400: invalid_json_schema | response_format | Invalid schema for response_format")
    assert "k-secret-123" not in error.detail and "fake-endpoint-xyz" not in error.detail


def test_a_400_is_logged_with_its_message(tmp_path, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="ports.llm.azure_openai"):
        _refusal(tmp_path)
    assert "Invalid schema for response_format" in caplog.text and "k-secret-123" not in caplog.text
