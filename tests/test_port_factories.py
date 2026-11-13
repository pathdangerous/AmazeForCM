"""Each port's factory returns the adapter its selector names; the default is the local adapter."""

import importlib

import pytest

from ports.config import Settings

# (factory, selector field, value, expected class). Each port adds its rows when it is built.
CASES = [
    ("ports.secrets:get_secrets", "secrets_provider", "env", "ports.secrets.env:EnvSecrets"),
    ("ports.secrets:get_secrets", "secrets_provider", "key_vault", "ports.secrets.key_vault:KeyVaultSecrets"),
    ("ports.llm:get_llm_provider", "llm_provider", "mock", "ports.llm.mock:MockProvider"),
    ("ports.llm:get_llm_provider", "llm_provider", "azure_openai", "ports.llm.azure_openai:AzureOpenAIProvider"),
    ("ports.storage:get_blob_store", "storage_provider", "local", "ports.storage.local:LocalBlobStore"),
    ("ports.storage:get_blob_store", "storage_provider", "azure_blob", "ports.storage.azure_blob:AzureBlobStore"),
    ("ports.scheduler:get_scheduler", "scheduler_provider", "local", "ports.scheduler.local:LocalScheduler"),
    ("ports.scheduler:get_scheduler", "scheduler_provider", "azure_functions",
     "ports.scheduler.azure_functions:AzureFunctionsScheduler"),
    ("ports.identity:get_identity", "identity_provider", "dev", "ports.identity.dev:DevIdentity"),
    ("ports.identity:get_identity", "identity_provider", "entra_id", "ports.identity.entra_id:EntraIdIdentity"),
    ("ports.notify:get_notifier", "notify_provider", "inapp", "ports.notify.inapp:InAppNotifier"),
    ("ports.notify:get_notifier", "notify_provider", "email", "ports.notify.email_teams:EmailNotifier"),
]

DEFAULTS = {
    "ports.secrets:get_secrets": "ports.secrets.env:EnvSecrets",
    "ports.llm:get_llm_provider": "ports.llm.mock:MockProvider",
    "ports.storage:get_blob_store": "ports.storage.local:LocalBlobStore",
    "ports.scheduler:get_scheduler": "ports.scheduler.local:LocalScheduler",
    "ports.identity:get_identity": "ports.identity.dev:DevIdentity",
    "ports.notify:get_notifier": "ports.notify.inapp:InAppNotifier",
}


def _load(ref: str):
    module_name, attr = ref.split(":")
    return getattr(importlib.import_module(module_name), attr)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for key in ("LLM_PROVIDER", "STORAGE_PROVIDER", "SCHEDULER_PROVIDER",
                "IDENTITY_PROVIDER", "SECRETS_PROVIDER", "NOTIFY_PROVIDER"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize(("factory", "field", "value", "expected"), CASES,
                         ids=[f"{c[1]}={c[2]}" for c in CASES])
def test_factory_returns_selected_adapter(factory, field, value, expected):
    settings = Settings(_env_file=None, **{field: value})
    assert type(_load(factory)(settings)) is _load(expected)


@pytest.mark.parametrize(("factory", "expected"), sorted(DEFAULTS.items()))
def test_factory_default_is_local_adapter(factory, expected):
    settings = Settings(_env_file=None)
    assert type(_load(factory)(settings)) is _load(expected)
