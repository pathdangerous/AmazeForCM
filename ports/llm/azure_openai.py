"""Azure OpenAI LLMProvider. STUB: implement after the move to the office laptop.

Plan for the implementation: the openai package's AzureOpenAI client with AZURE_OPENAI_ENDPOINT and
AZURE_OPENAI_API_VERSION; model = AZURE_OPENAI_DEPLOYMENT. Auth: DefaultAzureCredential token
provider when AZURE_OPENAI_AUTH=entra, else AZURE_OPENAI_API_KEY. Use structured outputs against
response_schema, store the raw response in the BlobStore and return its key as raw_ref.
Errors: raise ports.llm.base.LLMUnavailable, never the SDK's own errors: "timeout" for a timeout,
"rate_limit" for HTTP 429, "provider_error" for any other failed call. The API answers 503.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ports.config import AZURE_NOT_IMPLEMENTED, DEFAULT_ENV_FILE, read_env
from ports.llm.base import LLMResult, Message


class AzureOpenAIProvider:
    name = "azure_openai"
    is_stub = True  # /api/health reports this adapter as unavailable
    ENV_KEYS = (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_AUTH",
        "AZURE_OPENAI_API_KEY",
    )

    def __init__(self, env_file: Path | None = DEFAULT_ENV_FILE):
        self.config = read_env(self.ENV_KEYS, env_file=env_file)

    def complete(
        self,
        *,
        prompt_id: str,
        prompt_version: str,
        messages: list[Message],
        response_schema: type[BaseModel],
        temperature: float = 0.0,
    ) -> LLMResult:
        raise NotImplementedError(AZURE_NOT_IMPLEMENTED)
