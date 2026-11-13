"""Secrets port, local adapter: values from the .env file, overridden by the process environment."""

import pytest

from ports.secrets.base import SecretNotFound
from ports.secrets.env import EnvSecrets


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "AMAZE_TEST_SECRET=from-file   # trailing comment\nAMAZE_TEST_EMPTY=\n",
        encoding="utf-8",
    )
    return path


def test_reads_value_from_env_file(env_file, monkeypatch):
    monkeypatch.delenv("AMAZE_TEST_SECRET", raising=False)
    assert EnvSecrets(env_file).get("AMAZE_TEST_SECRET") == "from-file"


def test_process_environment_overrides_file(env_file, monkeypatch):
    monkeypatch.setenv("AMAZE_TEST_SECRET", "from-environment")
    assert EnvSecrets(env_file).get("AMAZE_TEST_SECRET") == "from-environment"


def test_missing_secret_raises_secret_not_found(env_file):
    with pytest.raises(SecretNotFound, match="AMAZE_TEST_NOT_THERE"):
        EnvSecrets(env_file).get("AMAZE_TEST_NOT_THERE")


def test_empty_secret_counts_as_missing(env_file, monkeypatch):
    monkeypatch.delenv("AMAZE_TEST_EMPTY", raising=False)
    with pytest.raises(SecretNotFound):
        EnvSecrets(env_file).get("AMAZE_TEST_EMPTY")


def test_missing_env_file_still_reads_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AMAZE_TEST_SECRET", "only-in-environment")
    assert EnvSecrets(tmp_path / "missing.env").get("AMAZE_TEST_SECRET") == "only-in-environment"


def test_name():
    assert EnvSecrets.name == "env"
