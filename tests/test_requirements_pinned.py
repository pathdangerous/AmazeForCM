"""Phase 7.8: every requirement is pinned with ==, and requirements.txt names the versions that are installed
(the packages were chosen with pip --uploaded-prior-to, so a drift here means the venv and the file disagree)."""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FILES = ("requirements.txt", "requirements-dev.txt")
PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9,_-]+\])?==(?P<version>[A-Za-z0-9.!+_-]+)$")


def requirement_lines(name: str) -> list[str]:
    lines = (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith(("#", "-r"))]


@pytest.mark.parametrize("name", FILES)
def test_every_requirement_is_pinned_with_double_equals(name):
    lines = requirement_lines(name)
    assert lines, name
    unpinned = [line for line in lines if not PIN.match(line)]
    assert not unpinned, f"{name}: not name==version: {unpinned}"


def test_requirements_txt_matches_the_installed_versions():
    wrong = []
    for line in requirement_lines("requirements.txt"):
        match = PIN.match(line)
        installed = metadata.version(match["name"])
        if installed != match["version"]:
            wrong.append(f"{match['name']}: file {match['version']}, installed {installed}")
    assert not wrong, wrong


@pytest.mark.parametrize("name", ["openai", "openpyxl", "pdfplumber"])
def test_the_phase_78_packages_are_pinned(name):
    pinned = {PIN.match(line)["name"].lower() for line in requirement_lines("requirements.txt")}
    assert name in pinned
