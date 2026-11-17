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


# ---- phase 3b: release after N failed days (assumption 51)


@pytest.mark.parametrize("value, expected", [(None, 3), ("1", 1), ("7", 7)])
def test_source_down_release_days_is_a_whole_number_of_at_least_one(tmp_path, monkeypatch, value, expected):
    monkeypatch.delenv("SOURCE_DOWN_RELEASE_DAYS", raising=False)
    if value is not None:
        monkeypatch.setenv("SOURCE_DOWN_RELEASE_DAYS", value)
    assert load_settings(tmp_path / "missing.env").source_down_release_days == expected


@pytest.mark.parametrize("value, expected", [(None, 300), ("1", 1), ("900", 900)])
def test_the_glossary_run_lease_is_a_whole_number_of_seconds_of_at_least_one(tmp_path, monkeypatch, value, expected):
    monkeypatch.delenv("GLOSSARY_RUN_LEASE_SECONDS", raising=False)
    if value is not None:
        monkeypatch.setenv("GLOSSARY_RUN_LEASE_SECONDS", value)
    assert load_settings(tmp_path / "missing.env").glossary_run_lease_seconds == expected


@pytest.mark.parametrize("bad", ["0", "-5", "1.5", "soon"])
def test_a_bad_glossary_run_lease_is_refused_by_name(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("GLOSSARY_RUN_LEASE_SECONDS", bad)
    with pytest.raises(ConfigError, match="GLOSSARY_RUN_LEASE_SECONDS"):
        load_settings(tmp_path / "missing.env")


def test_env_example_names_the_glossary_run_lease(tmp_path):
    assert "GLOSSARY_RUN_LEASE_SECONDS=300" in (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


@pytest.mark.parametrize("bad", ["0", "-2", "2.5", "three"])
def test_a_bad_source_down_release_days_is_refused_by_name(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("SOURCE_DOWN_RELEASE_DAYS", bad)
    with pytest.raises(ConfigError, match="SOURCE_DOWN_RELEASE_DAYS"):
        load_settings(tmp_path / "missing.env")


# ---- phase 6: the rule time limit (assumption 6) and the hypercare period (assumption 25)


@pytest.mark.parametrize(("name", "field", "default"), [("RULE_TIMEOUT_SECONDS", "rule_timeout_seconds", 5),
                                                         ("HYPERCARE_DAYS", "hypercare_days", 10)])
def test_phase_6_settings_default_and_are_whole_numbers_of_at_least_one(tmp_path, monkeypatch, name, field, default):
    monkeypatch.delenv(name, raising=False)
    assert getattr(load_settings(tmp_path / "missing.env"), field) == default
    monkeypatch.setenv(name, "1")
    assert getattr(load_settings(tmp_path / "missing.env"), field) == 1
    for bad in ("0", "-1", "1.5", "soon"):
        monkeypatch.setenv(name, bad)
        with pytest.raises(ConfigError, match=name):
            load_settings(tmp_path / "missing.env")


def test_env_example_names_the_phase_6_settings():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "RULE_TIMEOUT_SECONDS=5" in text and "HYPERCARE_DAYS=10" in text
