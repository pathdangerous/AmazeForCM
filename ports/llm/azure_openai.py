"""Azure OpenAI LLMProvider (LLM_PROVIDER=azure_openai), phase 7.8 plan assumptions 2 to 8.

The openai package's AzureOpenAI client, one deployment per call (AZURE_OPENAI_DEPLOYMENT; the `intent` and
`discovery_assistant` prompts use AZURE_OPENAI_DEPLOYMENT_SMALL when it is set). Auth: the API key by default,
or DefaultAzureCredential when AZURE_OPENAI_AUTH=entra (azure-identity is imported only then). Structured
outputs against response_schema; the whole chat completion is stored in the BlobStore and its key is raw_ref.

Errors: the SDK's own errors never leave complete(). LLMUnavailable("timeout" | "rate_limit" | "provider_error" |
"malformed_output") when the model did not answer usably, LLMRefused("content_filter" | "refusal") when it
refused. No exception message holds a key, the endpoint, response text or prompt text: `detail` is a status
code or a class name only. A missing setting is a ConfigError that names the KEY, never a value.

Strict schemas: response_format_for() asks the SDK for a strict json_schema (openai.lib, re-exported there as
type_to_response_format_param, which calls the private to_strict_json_schema). If the SDK cannot make one for a
schema, the request falls back to strict=False with response_schema.model_json_schema(); the output is
validated against the Pydantic model either way. tests/test_port_llm_azure.py checks every agent output
schema takes the strict path; none needed the fallback when this was written.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import openai
from openai.lib import type_to_response_format_param
from pydantic import BaseModel

from ports.config import ConfigError, DEFAULT_ENV_FILE, Settings, get_settings, read_env
from ports.llm.base import LLMRefused, LLMResult, LLMUnavailable, Message, TextPart
from ports.storage.base import BlobStore

log = logging.getLogger(__name__)

SMALL_DEPLOYMENT_PROMPTS = frozenset({"intent", "discovery_assistant"})
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
RATE_LIMIT_RETRIES = 2  # waits on a 429 with an acceptable Retry-After
MALFORMED_RETRIES = 1  # one more full call for an answer that is not valid JSON or not the schema


# Keywords Azure strict structured outputs do not take (a value check, not a shape): dropped from the request. The answer
# is still validated against the real Pydantic model, so a value outside them is a malformed answer, retried once.
UNSUPPORTED_KEYWORDS = frozenset({
    "default", "minLength", "maxLength", "pattern", "format", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "multipleOf", "minItems", "maxItems", "uniqueItems"})
_NAME_MAPS = ("properties", "$defs")  # their keys are field or definition names, not keywords


def _for_azure_strict(node: Any) -> Any:
    """Azure strict structured outputs accept anyOf, not oneOf, and no discriminator keyword (a Pydantic discriminated
    union emits both; the first live TransformationAgent call got HTTP 400), and none of UNSUPPORTED_KEYWORDS (the
    live ProfilerAgent call got HTTP 400). Rewritten everywhere, $defs included.
    Only the request changes: the answer is still validated against the real Pydantic model in _read_answer()."""
    if isinstance(node, dict):
        return {("anyOf" if key == "oneOf" else key):
                {name: _for_azure_strict(child) for name, child in value.items()} if key in _NAME_MAPS
                else _for_azure_strict(value)
                for key, value in node.items() if key != "discriminator" and key not in UNSUPPORTED_KEYWORDS}
    if isinstance(node, list):
        return [_for_azure_strict(value) for value in node]
    return node


def response_format_for(response_schema: type[BaseModel]) -> dict[str, Any]:
    """A strict json_schema response_format; the non-strict fallback only if the SDK cannot make a strict one."""
    try:
        fmt = dict(type_to_response_format_param(response_schema))
        fmt["json_schema"] = {**fmt["json_schema"], "schema": _for_azure_strict(fmt["json_schema"]["schema"])}
        return fmt
    except Exception:  # noqa: BLE001 - any refusal of the strict conversion
        return {"type": "json_schema", "json_schema": {"name": response_schema.__name__,
                                                       "schema": response_schema.model_json_schema(),
                                                       "strict": False}}


def _wire_content(message: Message) -> str | list[dict[str, Any]]:
    """A string stays a string; parts become the chat API's content parts (images as data URLs)."""
    if isinstance(message.content, str):
        return message.content
    return [{"type": "text", "text": part.text} if isinstance(part, TextPart)
            else {"type": "image_url", "image_url": {"url": f"data:{part.media_type};base64,{part.data_b64}"}}
            for part in message.content]


def _resource_endpoint(configured: str) -> str:
    """The resource's base URL (scheme and host). The SDK appends /openai/deployments/<deployment>/... itself, so a full
    URL copied from the portal (a Responses or chat path, an api-version query) must not be passed on: the first live
    call (phase 7.8, S8) failed with HTTP 400 for exactly that. The API version always comes from its own setting."""
    parts = urlsplit(configured.strip())
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else configured.strip()


# Deployments whose model refused a temperature, kept for the life of the process (the API builds a provider per
# request, so a per-instance record was forgotten at once). Keyed by (resource endpoint, deployment).
_TEMPERATURE_REFUSED: set[tuple[str, str]] = set()
_TEMPERATURE_LOCK = threading.Lock()


def forget_temperature_refusals() -> None:
    """Clear the process record (tests only)."""
    with _TEMPERATURE_LOCK:
        _TEMPERATURE_REFUSED.clear()


class _BadAnswer(Exception):
    """An answer that is not valid JSON, not the schema, or was cut off (internal; retried once)."""


class AzureOpenAIProvider:
    name = "azure_openai"
    is_stub = False
    requests_made = 0  # class default; every instance counts its own
    ENV_KEYS = (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_DEPLOYMENT_SMALL",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_AUTH",
        "AZURE_OPENAI_API_KEY",
    )

    def __init__(
        self,
        env_file: Path | None = DEFAULT_ENV_FILE,
        *,
        blob_store: BlobStore | None = None,
        settings: Settings | None = None,
        http_client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = read_env(self.ENV_KEYS, env_file=env_file)
        self._blob_store = blob_store
        self._settings = settings or get_settings()
        self._http_client = http_client  # an httpx2.Client; tests give one over a MockTransport
        self._sleep = sleep
        self._client: openai.AzureOpenAI | None = None
        self.requests_made = 0  # HTTP requests sent, retries included (read by the live evaluation)

    # ---- configuration

    def _auth_mode(self) -> str:
        mode = self.config["AZURE_OPENAI_AUTH"].strip().lower() or "key"
        if mode not in ("key", "entra"):
            raise ConfigError("AZURE_OPENAI_AUTH must be key or entra")
        return mode

    def missing(self) -> list[str]:
        """Names of the settings that are not set (names only, never values)."""
        names = [key for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION")
                 if not self.config[key].strip()]
        auth = self.config["AZURE_OPENAI_AUTH"].strip().lower()
        if auth in ("", "key"):
            if not self.config["AZURE_OPENAI_API_KEY"].strip():
                names.append("AZURE_OPENAI_API_KEY")
        elif auth != "entra":
            names.append("AZURE_OPENAI_AUTH")  # not key or entra
        return names

    @property
    def available(self) -> bool:
        """Configured to make a call (no call is made to find out)."""
        return not self.missing()

    def _deployment(self, prompt_id: str) -> str:
        small = self.config["AZURE_OPENAI_DEPLOYMENT_SMALL"].strip()
        if prompt_id in SMALL_DEPLOYMENT_PROMPTS and small:
            return small
        return self.config["AZURE_OPENAI_DEPLOYMENT"].strip()

    def _sdk_client(self) -> openai.AzureOpenAI:
        if self._client is None:
            mode = self._auth_mode()
            if mode == "entra":
                try:
                    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
                except ImportError:
                    raise ConfigError(
                        "AZURE_OPENAI_AUTH=entra needs the azure-identity package, which is not installed") from None
                credentials: dict[str, Any] = {
                    "azure_ad_token_provider": get_bearer_token_provider(DefaultAzureCredential(), TOKEN_SCOPE)}
            else:
                credentials = {"api_key": self.config["AZURE_OPENAI_API_KEY"].strip()}
            self._client = openai.AzureOpenAI(
                azure_endpoint=_resource_endpoint(self.config["AZURE_OPENAI_ENDPOINT"]),
                api_version=self.config["AZURE_OPENAI_API_VERSION"].strip(),
                max_retries=0,  # this class decides what is retried, and how often
                timeout=self._settings.llm_timeout_seconds,
                http_client=self._http_client,
                **credentials,
            )
        return self._client

    # ---- the call

    def complete(
        self,
        *,
        prompt_id: str,
        prompt_version: str,
        messages: list[Message],
        response_schema: type[BaseModel],
        temperature: float = 0.0,
    ) -> LLMResult:
        missing = self.missing()
        if missing:
            raise ConfigError("Azure OpenAI is not configured; missing: " + ", ".join(missing))
        client = self._sdk_client()
        deployment = self._deployment(prompt_id)
        request = {
            "model": deployment,
            "messages": [{"role": m.role, "content": _wire_content(m)} for m in messages],
            "temperature": temperature,
            "response_format": response_format_for(response_schema),
        }
        refused_key = (_resource_endpoint(self.config["AZURE_OPENAI_ENDPOINT"]), deployment)
        with _TEMPERATURE_LOCK:
            if refused_key in _TEMPERATURE_REFUSED:
                del request["temperature"]
        started = time.monotonic()
        rate_waits = bad_answers = 0
        while True:
            try:
                self.requests_made += 1
                completion = client.chat.completions.create(**request)
            except openai.RateLimitError as exc:
                wait = self._retry_after(exc)
                if wait is None or wait > self._settings.llm_retry_after_cap_seconds or rate_waits >= RATE_LIMIT_RETRIES:
                    raise LLMUnavailable("rate_limit") from None
                rate_waits += 1
                self._sleep(wait)
                continue
            except openai.APITimeoutError:
                raise LLMUnavailable("timeout") from None
            except openai.APIStatusError as exc:
                if exc.status_code == 400 and self._is_content_filter(exc):
                    raise LLMRefused("content_filter") from None
                if exc.status_code == 400 and "temperature" in request and self._refuses_temperature(exc):
                    # a model that takes only its default temperature (a reasoning model): ask again without it, and
                    # leave it out for this deployment from now on (phase 7.8, S8: the second live failure)
                    with _TEMPERATURE_LOCK:
                        _TEMPERATURE_REFUSED.add(refused_key)
                    del request["temperature"]
                    continue
                detail = str(exc.status_code)
                if exc.status_code == 400:  # a refused request: Azure says why (the schema, a parameter), so the log can
                    detail += f": {self._error_message(exc)}"
                    log.warning("Azure OpenAI refused the %s request: %s", prompt_id, detail)
                raise LLMUnavailable("provider_error", detail) from None
            except openai.APIError as exc:
                raise LLMUnavailable("provider_error", type(exc).__name__) from None

            raw_ref = self._store_raw(prompt_id, completion)
            try:
                output, model_version = self._read_answer(completion, response_schema)
            except _BadAnswer:
                if bad_answers >= MALFORMED_RETRIES:
                    raise LLMUnavailable("malformed_output") from None
                bad_answers += 1
                continue
            return LLMResult(
                output=output, provider=self.name, model=deployment, model_version=model_version,
                prompt_id=prompt_id, prompt_version=prompt_version,
                latency_ms=max(0, round((time.monotonic() - started) * 1000)), raw_ref=raw_ref,
            )

    # ---- helpers

    @staticmethod
    def _read_answer(completion: Any, response_schema: type[BaseModel]) -> tuple[BaseModel, str]:
        """The validated output and the model version, or LLMRefused, or _BadAnswer."""
        if not completion.choices:
            raise _BadAnswer
        choice = completion.choices[0]
        if choice.finish_reason == "content_filter":
            raise LLMRefused("content_filter")
        message = choice.message
        if getattr(message, "refusal", None):
            raise LLMRefused("refusal")
        if choice.finish_reason == "length" or not message.content:
            raise _BadAnswer
        try:
            return response_schema.model_validate_json(message.content), completion.model or ""
        except ValueError:  # not JSON, or not this schema (pydantic's ValidationError is a ValueError)
            raise _BadAnswer from None

    def _store_raw(self, prompt_id: str, completion: Any) -> str | None:
        if self._blob_store is None:
            return None
        key = f"llm-raw/{prompt_id}/{uuid.uuid4()}.json"
        self._blob_store.put(key, completion.model_dump_json().encode("utf-8"))
        return key

    def _error_message(self, exc: openai.APIStatusError) -> str:
        """Azure's own error text (code, param, message) of a 400, at most 400 characters, with the key and the endpoint
        taken out. Azure does not echo the prompt in a refused request; the answer body is not read."""
        body = exc.body if isinstance(exc.body, dict) else {}
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        parts = [str(inner[k]) for k in ("code", "param", "message") if inner.get(k)]
        text = " | ".join(parts) or "no message"
        for secret in (self.config["AZURE_OPENAI_API_KEY"].strip(), self.config["AZURE_OPENAI_ENDPOINT"].strip(),
                       urlsplit(self.config["AZURE_OPENAI_ENDPOINT"].strip()).netloc):
            if secret:
                text = text.replace(secret, "[removed]")
        return text[:400]

    @staticmethod
    def _refuses_temperature(exc: openai.APIStatusError) -> bool:
        body = exc.body if isinstance(exc.body, dict) else {}
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        return inner.get("param") == "temperature" and inner.get("code") in ("unsupported_value", "unsupported_parameter")

    @staticmethod
    def _is_content_filter(exc: openai.APIStatusError) -> bool:
        body = exc.body if isinstance(exc.body, dict) else {}
        inner = body.get("error") if isinstance(body.get("error"), dict) else {}
        return "content_filter" in (exc.code, body.get("code"), inner.get("code"))

    @staticmethod
    def _retry_after(exc: openai.RateLimitError) -> float | None:
        """Seconds to wait: retry-after-ms, else Retry-After in seconds. None when absent or not a number
        (an HTTP-date is not read)."""
        headers = exc.response.headers
        for name, scale in (("retry-after-ms", 1000), ("retry-after", 1)):
            raw = headers.get(name)
            if raw is None:
                continue
            try:
                value = float(raw) / scale
            except ValueError:
                continue
            if math.isfinite(value) and value >= 0:
                return value
        return None
