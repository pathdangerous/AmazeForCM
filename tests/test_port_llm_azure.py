"""AzureOpenAIProvider (phase 7.8, task 1.2) on recorded synthetic responses.

No network: the SDK client gets an httpx2.Client over a MockTransport that serves fixtures/llm_recorded/azure/.
The fake key and endpoint below must appear in no exception message, and neither may response or prompt text.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx2
import pytest

from ports.config import ConfigError, Settings
from ports.llm.azure_openai import AzureOpenAIProvider, forget_temperature_refusals
from ports.llm.base import LLMRefused, LLMResult, LLMUnavailable, Message
from ports.storage.local import LocalBlobStore
from core.domain.profiling import ProfilerOutput

RECORDED = Path(__file__).resolve().parents[1] / "fixtures" / "llm_recorded" / "azure"
FAKE_KEY = "fake-key-0123456789abcdef"
ENDPOINT = "https://fake-endpoint-xyz.example.invalid"
MESSAGES = [Message(role="system", content="SYNTHETIC-SYSTEM-PROMPT"), Message(role="user", content="SYNTHETIC-USER-PROMPT")]
LEAKS = (FAKE_KEY, "fake-endpoint-xyz", "SYNTHETIC-")


def recorded(name: str) -> dict:
    return json.loads((RECORDED / f"{name}.json").read_text(encoding="utf-8"))


def ok(name: str = "ok", *, status: int = 200, headers: dict | None = None):
    return ("response", status, recorded(name), headers or {})


class Server:
    """Serves one scripted step per request (the last repeats) and records every request."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        step = self.steps[min(len(self.requests), len(self.steps)) - 1]
        if step[0] == "raise":
            raise step[1](step[2], request=request)
        _, status, body, headers = step
        return httpx2.Response(status, json=body, headers=headers)

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in AzureOpenAIProvider.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    forget_temperature_refusals()  # the record is per process, so each test starts without it
    yield
    forget_temperature_refusals()


def make(tmp_path, *steps, small: str = "", auth: str = "", key: str = FAKE_KEY, with_blobs: bool = True,
         deployment: str = "main-deploy", version: str = "2026-01-01", settings: Settings | None = None,
         endpoint: str = ENDPOINT):
    env = tmp_path / "azure.env"
    lines = [f"AZURE_OPENAI_ENDPOINT={endpoint}", f"AZURE_OPENAI_DEPLOYMENT={deployment}",
             f"AZURE_OPENAI_DEPLOYMENT_SMALL={small}", f"AZURE_OPENAI_API_VERSION={version}",
             f"AZURE_OPENAI_AUTH={auth}", f"AZURE_OPENAI_API_KEY={key}"]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    server = Server(*steps)
    sleeps: list[float] = []
    store = LocalBlobStore(tmp_path / "blobs") if with_blobs else None
    provider = AzureOpenAIProvider(env, blob_store=store, settings=settings or Settings(_env_file=None),
                                   http_client=httpx2.Client(transport=httpx2.MockTransport(server)),
                                   sleep=sleeps.append)
    return provider, server, sleeps, store


def call(provider, prompt_id: str = "profiler", **kwargs):
    return provider.complete(prompt_id=prompt_id, prompt_version="1", messages=MESSAGES,
                             response_schema=ProfilerOutput, **kwargs)


# ---------------------------------------------------------------- the answer

def test_a_valid_answer_is_returned_with_its_provenance_and_raw_response(tmp_path):
    provider, server, sleeps, store = make(tmp_path, ok())
    result = call(provider)
    assert isinstance(result, LLMResult) and isinstance(result.output, ProfilerOutput)
    assert result.output.recommended_outcome == "meets_with_gaps"
    assert (result.provider, result.model, result.model_version) == ("azure_openai", "main-deploy", "gpt-4o-2026-08-06")
    assert (result.prompt_id, result.prompt_version) == ("profiler", "1")
    assert result.latency_ms >= 0
    assert result.raw_ref and result.raw_ref.startswith("llm-raw/profiler/") and result.raw_ref.endswith(".json")
    assert json.loads(store.get(result.raw_ref))["id"] == "chatcmpl-synthetic-0001"
    assert sleeps == []


def test_the_request_carries_the_deployment_temperature_and_a_strict_schema(tmp_path):
    provider, server, _, _ = make(tmp_path, ok())
    call(provider)
    request = server.requests[0]
    assert "/openai/deployments/main-deploy/chat/completions" in request.url.path
    assert request.url.params["api-version"] == "2026-01-01"
    body = server.body()
    assert body["temperature"] == 0
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "ProfilerOutput" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False


@pytest.mark.parametrize("endpoint", [
    ENDPOINT + "/openai/responses?api-version=2025-04-01-preview",  # a full URL copied from the portal (phase 7.8 S8)
    ENDPOINT + "/",
    ENDPOINT + "/openai/deployments/other/chat/completions?api-version=2024-01-01",
])
def test_an_endpoint_given_as_a_full_url_is_reduced_to_the_resource(tmp_path, endpoint):
    """The first live call (S8) failed with HTTP 400: the configured endpoint was a full Responses API URL, and the
    SDK appends its own /openai/deployments/... path to azure_endpoint. Only the scheme and the host are used now."""
    provider, server, _, _ = make(tmp_path, ok(), endpoint=endpoint)
    call(provider)
    url = server.requests[0].url
    assert url.host == "fake-endpoint-xyz.example.invalid"
    assert url.path.endswith("/openai/deployments/main-deploy/chat/completions") and "responses" not in url.path
    assert url.path.count("/openai/") == 1
    assert url.params["api-version"] == "2026-01-01"  # the setting, never the URL's own


def test_a_deployment_that_refuses_temperature_is_asked_again_without_it_and_remembered(tmp_path):
    """The second live failure (S8): the deployment's model takes only its default temperature (400 unsupported_value,
    param temperature). One more request without it; later calls of the provider leave it out at once."""
    provider, server, sleeps, _ = make(tmp_path, ok("error_400_unsupported_temperature", status=400), ok())
    first = call(provider)
    assert isinstance(first.output, ProfilerOutput) and len(server.requests) == 2
    assert server.body(0)["temperature"] == 0 and "temperature" not in server.body(1)
    call(provider)
    assert len(server.requests) == 3 and "temperature" not in server.body(2)
    assert sleeps == []


def test_the_temperature_refusal_is_remembered_per_process_not_per_provider_instance(tmp_path):
    """T1: a second provider (one per request in the API) for the same endpoint and deployment leaves temperature out
    at once: 3 requests in all for two calls, not 4. Another deployment is not affected."""
    for name in "abc":
        (tmp_path / name).mkdir()
    first, server_1, _, _ = make(tmp_path / "a", ok("error_400_unsupported_temperature", status=400), ok())
    second, server_2, _, _ = make(tmp_path / "b", ok())
    other, server_3, _, _ = make(tmp_path / "c", ok(), deployment="other-deploy")
    call(first)
    call(second)
    call(other)
    assert len(server_1.requests) + len(server_2.requests) == 3
    assert "temperature" not in server_2.body(0)
    assert server_3.body(0)["temperature"] == 0


def test_another_unsupported_value_is_still_a_provider_error(tmp_path):
    body = recorded("error_400_unsupported_temperature")
    body["error"]["param"] = "response_format"
    provider, server, _, _ = make(tmp_path, ("response", 400, body, {}))
    with pytest.raises(LLMUnavailable) as caught:
        call(provider)
    assert caught.value.reason == "provider_error" and len(server.requests) == 1


def test_image_parts_travel_as_data_urls_and_text_stays_a_string(tmp_path):
    """Task 2.3: a message of parts is sent as the chat API expects; a plain message is unchanged."""
    from ports.llm.base import ImagePart, TextPart

    provider, server, _, _ = make(tmp_path, ok())
    messages = [Message(role="system", content="SYNTHETIC-SYSTEM-PROMPT"),
                Message(role="user", content=[ImagePart(data_b64="QUJD"), ImagePart(data_b64="REVG"),
                                              TextPart(text="SYNTHETIC-USER-PROMPT")])]
    provider.complete(prompt_id="profiler", prompt_version="1", messages=messages, response_schema=ProfilerOutput)
    system, user = server.body()["messages"]
    assert system == {"role": "system", "content": "SYNTHETIC-SYSTEM-PROMPT"}
    assert user["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,REVG"}},
        {"type": "text", "text": "SYNTHETIC-USER-PROMPT"}]


def test_no_blob_store_means_no_raw_ref(tmp_path):
    provider, *_ = make(tmp_path, ok(), with_blobs=False)
    assert call(provider).raw_ref is None


# ---------------------------------------------------------------- the deployment

@pytest.mark.parametrize("prompt_id", ["intent", "discovery_assistant"])
def test_the_small_deployment_serves_the_two_light_prompts(tmp_path, prompt_id):
    provider, server, _, _ = make(tmp_path, ok(), small="small-deploy")
    result = call(provider, prompt_id)
    assert "/deployments/small-deploy/" in server.requests[0].url.path
    assert result.model == "small-deploy"


def test_other_prompts_use_the_main_deployment_even_when_a_small_one_is_set(tmp_path):
    provider, server, _, _ = make(tmp_path, ok(), small="small-deploy")
    assert call(provider, "mapping").model == "main-deploy"


@pytest.mark.parametrize("prompt_id", ["intent", "discovery_assistant"])
def test_without_a_small_deployment_the_main_one_is_used(tmp_path, prompt_id):
    provider, server, _, _ = make(tmp_path, ok(), small="")
    assert call(provider, prompt_id).model == "main-deploy"
    assert "/deployments/main-deploy/" in server.requests[0].url.path


# ---------------------------------------------------------------- unavailable

def test_a_timeout_is_llm_unavailable_timeout(tmp_path):
    provider, server, _, _ = make(tmp_path, ("raise", httpx2.ReadTimeout, "synthetic timeout"))
    with pytest.raises(LLMUnavailable) as caught:
        call(provider)
    assert caught.value.reason == "timeout"
    assert len(server.requests) == 1  # the SDK does not retry on its own


def test_a_429_with_a_short_retry_after_waits_then_succeeds(tmp_path):
    provider, server, sleeps, _ = make(tmp_path, ok("error_429", status=429, headers={"Retry-After": "3"}), ok())
    assert isinstance(call(provider).output, ProfilerOutput)
    assert sleeps == [3] and len(server.requests) == 2


def test_retry_after_ms_is_read_too(tmp_path):
    provider, _, sleeps, _ = make(tmp_path, ok("error_429", status=429, headers={"retry-after-ms": "1500"}), ok())
    call(provider)
    assert sleeps == [1.5]


def test_a_429_with_a_retry_after_over_the_cap_fails_at_once(tmp_path):
    provider, server, sleeps, _ = make(tmp_path, ok("error_429", status=429, headers={"Retry-After": "999"}))
    with pytest.raises(LLMUnavailable) as caught:
        call(provider)
    assert caught.value.reason == "rate_limit" and sleeps == [] and len(server.requests) == 1


def test_a_429_without_retry_after_fails_at_once(tmp_path):
    provider, server, sleeps, _ = make(tmp_path, ok("error_429", status=429))
    with pytest.raises(LLMUnavailable) as caught:
        call(provider)
    assert caught.value.reason == "rate_limit" and sleeps == [] and len(server.requests) == 1


def test_the_cap_is_the_setting(tmp_path):
    settings = Settings(_env_file=None, llm_retry_after_cap_seconds=2)
    provider, _, sleeps, _ = make(tmp_path, ok("error_429", status=429, headers={"Retry-After": "3"}), settings=settings)
    with pytest.raises(LLMUnavailable):
        call(provider)
    assert sleeps == []


def test_three_429s_within_the_cap_are_rate_limit_after_two_waits(tmp_path):
    provider, server, sleeps, _ = make(tmp_path, ok("error_429", status=429, headers={"Retry-After": "1"}))
    with pytest.raises(LLMUnavailable) as caught:
        call(provider)
    assert caught.value.reason == "rate_limit" and sleeps == [1, 1] and len(server.requests) == 3


def test_a_500_is_a_provider_error_naming_only_the_status(tmp_path):
    provider, server, _, _ = make(tmp_path, ok("error_500", status=500))
    with pytest.raises(LLMUnavailable) as caught:
        call(provider)
    assert caught.value.reason == "provider_error" and caught.value.detail == "500"
    assert len(server.requests) == 1


def test_a_connection_failure_is_a_provider_error(tmp_path):
    provider, *_ = make(tmp_path, ("raise", httpx2.ConnectError, "synthetic refused"))
    with pytest.raises(LLMUnavailable) as caught:
        call(provider)
    assert caught.value.reason == "provider_error"


# ---------------------------------------------------------------- refused

def test_a_400_content_filter_is_refused(tmp_path):
    provider, *_ = make(tmp_path, ok("error_400_content_filter", status=400))
    with pytest.raises(LLMRefused) as caught:
        call(provider)
    assert caught.value.reason == "content_filter"


def test_a_content_filter_finish_reason_is_refused(tmp_path):
    provider, *_ = make(tmp_path, ok("finish_content_filter"))
    with pytest.raises(LLMRefused) as caught:
        call(provider)
    assert caught.value.reason == "content_filter"


def test_a_refusal_message_is_refused(tmp_path):
    provider, server, _, _ = make(tmp_path, ok("refusal"))
    with pytest.raises(LLMRefused) as caught:
        call(provider)
    assert caught.value.reason == "refusal" and len(server.requests) == 1


# ---------------------------------------------------------------- malformed output

@pytest.mark.parametrize("bad", ["malformed", "schema_mismatch", "length"])
def test_a_bad_answer_once_then_a_good_one_succeeds_with_two_requests(tmp_path, bad):
    provider, server, _, _ = make(tmp_path, ok(bad), ok())
    assert isinstance(call(provider).output, ProfilerOutput)
    assert len(server.requests) == 2


@pytest.mark.parametrize("bad", ["malformed", "schema_mismatch", "length"])
def test_a_bad_answer_twice_is_malformed_output_and_both_raw_answers_are_kept(tmp_path, bad):
    provider, server, _, store = make(tmp_path, ok(bad))
    with pytest.raises(LLMUnavailable) as caught:
        call(provider)
    assert caught.value.reason == "malformed_output" and len(server.requests) == 2
    keys = store.list_keys("llm-raw/profiler/")
    assert len(keys) == 2 and all(k.endswith(".json") for k in keys)


# ---------------------------------------------------------------- configuration

def test_not_configured_raises_config_error_naming_keys_and_makes_no_request(tmp_path):
    env = tmp_path / "empty.env"
    env.write_text("", encoding="utf-8")
    server = Server(ok())
    provider = AzureOpenAIProvider(env, settings=Settings(_env_file=None),
                                   http_client=httpx2.Client(transport=httpx2.MockTransport(server)))
    assert provider.available is False
    assert provider.missing() == ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION",
                                  "AZURE_OPENAI_API_KEY"]
    with pytest.raises(ConfigError) as caught:
        call(provider)
    assert "AZURE_OPENAI_ENDPOINT" in str(caught.value) and "AZURE_OPENAI_API_KEY" in str(caught.value)
    assert server.requests == []


def test_available_with_a_key_and_with_entra(tmp_path):
    assert make(tmp_path, ok())[0].available is True
    assert make(tmp_path, ok(), key="")[0].available is False
    entra = make(tmp_path, ok(), auth="entra", key="")[0]
    assert entra.available is True and entra.missing() == []


def test_the_stub_flag_is_off():
    assert AzureOpenAIProvider.is_stub is False and AzureOpenAIProvider.name == "azure_openai"


def test_entra_without_azure_identity_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.identity", None)  # an import of it now raises ImportError
    provider, server, _, _ = make(tmp_path, ok(), auth="entra", key="")
    with pytest.raises(ConfigError) as caught:
        call(provider)
    assert "azure-identity" in str(caught.value) and server.requests == []


def test_an_unknown_auth_value_is_a_config_error(tmp_path):
    provider, server, _, _ = make(tmp_path, ok(), auth="magic")
    with pytest.raises(ConfigError):
        call(provider)
    assert server.requests == []


# ---------------------------------------------------------------- nothing sensitive in an exception

FAILURES = {
    "timeout": [("raise", httpx2.ReadTimeout, "t")],
    "rate": [ok("error_429", status=429)],
    "server": [ok("error_500", status=500)],
    "connect": [("raise", httpx2.ConnectError, "c")],
    "filter400": [ok("error_400_content_filter", status=400)],
    "filter_finish": [ok("finish_content_filter")],
    "refusal": [ok("refusal")],
    "malformed": [ok("malformed")],
    "mismatch": [ok("schema_mismatch")],
    "length": [ok("length")],
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_no_exception_carries_the_key_the_endpoint_or_response_or_prompt_text(tmp_path, case):
    provider, *_ = make(tmp_path, *FAILURES[case])
    with pytest.raises((LLMUnavailable, LLMRefused)) as caught:
        call(provider)
    shown = f"{caught.value!s} {caught.value!r} {getattr(caught.value, 'detail', '')}"
    assert not [leak for leak in LEAKS if leak in shown], shown


def test_a_config_error_never_shows_a_value(tmp_path):
    provider, *_ = make(tmp_path, ok(), auth="magic")
    with pytest.raises(ConfigError) as caught:
        call(provider)
    assert not [leak for leak in LEAKS if leak in str(caught.value)]


# ---------------------------------------------------------------- every agent output schema is strict-capable

def _agent_schemas() -> dict[str, type]:
    import importlib

    for name in ("discovery_assistant", "glossary_builder", "intent", "mapping", "profiler", "review_summariser",
                 "rule_recommender", "source_discovery", "table_extractor"):
        importlib.import_module(f"agents.{name}")
    from agents.base import Agent

    found, todo = {}, list(Agent.__subclasses__())
    while todo:
        cls = todo.pop()
        todo.extend(cls.__subclasses__())
        schema = getattr(cls, "output_schema", None)
        if schema is not None:
            found[schema.__name__] = schema
    return found


def test_the_registered_agents_have_output_schemas():
    assert len(_agent_schemas()) >= 7
    assert "ExtractedTable" in _agent_schemas()


@pytest.mark.parametrize("name", sorted(_agent_schemas()))
def test_every_agent_output_schema_converts_to_a_strict_json_schema(name):
    from ports.llm.azure_openai import response_format_for

    fmt = response_format_for(_agent_schemas()[name])
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True, f"{name} needed the non-strict fallback"
    assert fmt["json_schema"]["name"] == name


# ---------------------------------------------------------------- Azure strict mode: anyOf, never oneOf or discriminator

def _keys_anywhere(node, found: set[str] | None = None) -> set[str]:
    found = set() if found is None else found
    if isinstance(node, dict):
        found.update(node)
        for value in node.values():
            _keys_anywhere(value, found)
    elif isinstance(node, list):
        for value in node:
            _keys_anywhere(value, found)
    return found


def test_the_transformation_format_has_any_of_and_no_one_of_or_discriminator():
    from core.domain.transformation import TransformationOutput
    from ports.llm.azure_openai import response_format_for

    keys = _keys_anywhere(response_format_for(TransformationOutput)["json_schema"]["schema"])
    assert "anyOf" in keys
    assert "oneOf" not in keys and "discriminator" not in keys


def test_no_agent_or_evaluator_output_format_has_one_of_or_discriminator():
    import importlib

    from agents.evaluate_agents import EVALUATORS
    from agents.mapping import MappingEvaluator
    from ports.llm.azure_openai import response_format_for

    importlib.import_module("agents.transformation")
    schemas = dict(_agent_schemas())
    for cls in (*EVALUATORS.values(), MappingEvaluator):
        schemas[cls.output_schema.__name__] = cls.output_schema
    assert "TransformationOutput" in schemas
    for name, schema in sorted(schemas.items()):
        keys = _keys_anywhere(response_format_for(schema))
        assert not keys & {"oneOf", "discriminator"}, f"{name} still has {keys & {'oneOf', 'discriminator'}}"


def _transformation_answer(steps: list[dict]) -> dict:
    body = recorded("ok")
    body["choices"][0]["message"]["content"] = json.dumps({"columns": [{"source_field": "px", "steps": steps, "reason": "synthetic reason",
                                                                     "confidence": 0.9}]})
    return body


def test_a_valid_transformation_answer_still_validates_against_the_discriminated_union(tmp_path):
    from core.domain.transformation import Cast, TransformationOutput, Trim

    body = _transformation_answer([{"op": "trim"}, {"op": "cast", "type": "decimal"}])
    provider, server, _, _ = make(tmp_path, ("response", 200, body, {}))
    result = provider.complete(prompt_id="transformation", prompt_version="1", messages=MESSAGES,
                               response_schema=TransformationOutput)
    steps = result.output.columns[0].steps
    assert isinstance(steps[0], Trim) and isinstance(steps[1], Cast) and len(server.requests) == 1
    sent = _keys_anywhere(server.body()["response_format"])
    assert "anyOf" in sent and not sent & {"oneOf", "discriminator"}


@pytest.mark.parametrize("step", [{"op": "trim", "to": "px"}, {"op": "cast", "format": "YYYY-MM-DD"},
                                  {"op": "rename"}, {"op": "unknown"}])
def test_a_transformation_step_whose_op_does_not_match_its_fields_is_still_refused(tmp_path, step):
    from core.domain.transformation import TransformationOutput

    provider, server, _, _ = make(tmp_path, ("response", 200, _transformation_answer([step]), {}))
    with pytest.raises(LLMUnavailable) as caught:
        provider.complete(prompt_id="transformation", prompt_version="1", messages=MESSAGES,
                          response_schema=TransformationOutput)
    assert caught.value.reason == "malformed_output" and len(server.requests) == 2
