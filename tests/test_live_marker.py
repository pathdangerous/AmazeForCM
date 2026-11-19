"""Phase 7.8: the `live` marker (tests that call the real model) is registered and excluded from the default run."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = "tests/test_live_marker.py"
DUMMY = "test_live_marker_dummy_never_fails"


def _ini() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["tool"]["pytest"]["ini_options"]


def _collect(*extra: str) -> str:
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *extra, THIS_FILE],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    return done.stdout


@pytest.mark.live
def test_live_marker_dummy_never_fails():
    """Selected only by `pytest -m live`; it calls no model and cannot fail."""
    assert True


def test_pyproject_declares_the_live_marker_and_excludes_it_by_default():
    ini = _ini()
    assert any(entry.split(":")[0].strip() == "live" for entry in ini["markers"])
    assert "-m \"not live\"" in ini["addopts"] or "-m 'not live'" in ini["addopts"]
    assert "--strict-markers" in ini["addopts"]


def test_the_default_run_does_not_collect_a_live_test():
    assert DUMMY not in _collect()


def test_dash_m_live_collects_only_the_live_tests():
    out = _collect("-m", "live")
    assert DUMMY in out
    assert "test_the_default_run_does_not_collect_a_live_test" not in out
