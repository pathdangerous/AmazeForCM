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


def test_the_stale_load_limit_defaults_to_thirty_minutes_and_is_a_whole_number_of_at_least_one(tmp_path, monkeypatch):
    """Phase 7 assumption 38."""
    monkeypatch.delenv("LOAD_STALE_MINUTES", raising=False)
    assert load_settings(tmp_path / "missing.env").load_stale_minutes == 30
    monkeypatch.setenv("LOAD_STALE_MINUTES", "1")
    assert load_settings(tmp_path / "missing.env").load_stale_minutes == 1
    for bad in ("0", "-1", "1.5", "soon"):
        monkeypatch.setenv("LOAD_STALE_MINUTES", bad)
        with pytest.raises(ConfigError, match="LOAD_STALE_MINUTES"):
            load_settings(tmp_path / "missing.env")
    assert "LOAD_STALE_MINUTES=30" in (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


def test_the_release_threshold_defaults_to_ninety_and_is_between_one_and_a_hundred(tmp_path, monkeypatch):
    """Phase 7 assumption 38."""
    from decimal import Decimal

    monkeypatch.delenv("TRUST_RELEASE_THRESHOLD", raising=False)
    assert load_settings(tmp_path / "missing.env").trust_release_threshold == Decimal("90")
    for good, expected in (("1", "1"), ("95.5", "95.5"), ("100", "100")):
        monkeypatch.setenv("TRUST_RELEASE_THRESHOLD", good)
        assert load_settings(tmp_path / "missing.env").trust_release_threshold == Decimal(expected)
    for bad in ("0", "0.9", "100.1", "soon"):
        monkeypatch.setenv("TRUST_RELEASE_THRESHOLD", bad)
        with pytest.raises(ConfigError, match="TRUST_RELEASE_THRESHOLD"):
            load_settings(tmp_path / "missing.env")
    assert "TRUST_RELEASE_THRESHOLD=90" in (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


def test_phase_7_8_model_settings_default(tmp_path, monkeypatch):
    for name in ("LLM_TIMEOUT_SECONDS", "LLM_RETRY_AFTER_CAP_SECONDS", "MAX_MODEL_CALLS_PER_ONBOARDING"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings(tmp_path / "missing.env")
    assert settings.llm_timeout_seconds == 60
    assert settings.llm_retry_after_cap_seconds == 20
    assert settings.max_model_calls_per_onboarding == 60


def test_a_model_setting_is_read_from_an_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("LLM_RETRY_AFTER_CAP_SECONDS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_TIMEOUT_SECONDS=5\nLLM_RETRY_AFTER_CAP_SECONDS=0\n", encoding="utf-8")
    settings = load_settings(env_file)
    assert settings.llm_timeout_seconds == 5
    assert settings.llm_retry_after_cap_seconds == 0


def test_a_zero_llm_timeout_is_refused_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0")
    with pytest.raises(ConfigError, match="LLM_TIMEOUT_SECONDS"):
        load_settings(tmp_path / "missing.env")


def test_env_example_names_the_phase_6_settings():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "RULE_TIMEOUT_SECONDS=5" in text and "HYPERCARE_DAYS=10" in text


def _example_assignments() -> dict[str, str]:
    """KEY -> value (inline comment removed) of every assignment in .env.example."""
    found = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            found[key.strip()] = value.split("#", 1)[0].strip()
    return found


def test_env_example_names_every_setting_with_its_default():
    """Phase 7.8: every Settings field is in .env.example, and its value there is the default (so a copied
    .env.example changes nothing). Paths are compared as written; the four selectors are checked above."""
    example = _example_assignments()
    missing = sorted(name.upper() for name in Settings.model_fields if name.upper() not in example)
    assert not missing, f"add to .env.example: {missing}"
    defaults = Settings(_env_file=None)
    for name in ("require_four_eyes", "send_sample_values", "upload_max_mb", "auto_accept_confidence",
                 "onboarding_job_lease_seconds", "llm_timeout_seconds", "llm_retry_after_cap_seconds",
                 "max_model_calls_per_onboarding"):
        value = getattr(defaults, name)
        written = example[name.upper()]
        assert (written.lower() == str(value).lower()) if isinstance(value, bool) else float(written) == float(value), (
            name, written, value)


def test_env_example_names_every_model_adapter_key_and_auth_defaults_to_key():
    from ports.llm.azure_openai import AzureOpenAIProvider

    example = _example_assignments()
    assert set(AzureOpenAIProvider.ENV_KEYS) <= set(example)
    assert example["AZURE_OPENAI_AUTH"] == "key"  # the adapter's default (ports/llm/azure_openai.py)
