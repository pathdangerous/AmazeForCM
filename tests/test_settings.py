"""Settings: the six adapter selectors and LOCAL_BLOB_ROOT, read from .env and the environment."""

import shutil
from pathlib import Path

import pytest

from ports.config import ConfigError, Settings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
SELECTORS = {
    "LLM_PROVIDER": "mock",
    "STORAGE_PROVIDER": "local",
    "SCHEDULER_PROVIDER": "local",
    "IDENTITY_PROVIDER": "dev",
    "SECRETS_PROVIDER": "env",
    "NOTIFY_PROVIDER": "inapp",
}


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for key in [*SELECTORS, "LOCAL_BLOB_ROOT"]:
        monkeypatch.delenv(key, raising=False)


def _as_dict(settings: Settings) -> dict[str, str]:
    return {key: getattr(settings, key.lower()) for key in SELECTORS}


def test_env_example_gives_local_defaults(tmp_path):
    """Also proves the inline comments in .env.example are not read as part of the value."""
    env_file = tmp_path / ".env"
    shutil.copy(REPO_ROOT / ".env.example", env_file)
    settings = load_settings(env_file)
    assert _as_dict(settings) == SELECTORS
    assert settings.local_blob_root == Path("./data/blob")


def test_no_env_file_gives_local_defaults(tmp_path):
    settings = load_settings(tmp_path / "missing.env")
    assert _as_dict(settings) == SELECTORS


def test_environment_variable_overrides_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    shutil.copy(REPO_ROOT / ".env.example", env_file)
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")
    assert load_settings(env_file).llm_provider == "azure_openai"


@pytest.mark.parametrize("key", sorted(SELECTORS))
def test_unknown_selector_value_names_variable_and_allowed_values(tmp_path, monkeypatch, key):
    monkeypatch.setenv(key, "not_a_real_adapter")
    with pytest.raises(ConfigError) as excinfo:
        load_settings(tmp_path / "missing.env")
    message = str(excinfo.value)
    assert key in message
    assert "not_a_real_adapter" in message
    assert SELECTORS[key] in message  # the local value is always among the allowed values
