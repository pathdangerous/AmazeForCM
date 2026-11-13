"""The project targets Python 3.14 only (CLAUDE.md, Tech stack)."""

import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_interpreter_is_python_3_14():
    assert sys.version_info[:2] == (3, 14), (
        f"Running on Python {sys.version.split()[0]}; this project needs 3.14. "
        "Recreate the venv with: py -3.14 -m venv .venv"
    )


def test_pyproject_requires_python_3_14():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["requires-python"] == ">=3.14,<3.15"
