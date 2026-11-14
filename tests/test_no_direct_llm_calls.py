"""No model call outside Agent.run() (phase 3 plan, assumption 23; carried item 1).

A static check: no file under core/, apps/ or agents/ other than agents/base.py calls `.complete(` or
reaches `complete` through getattr. It parses source; it runs nothing. The checker tests itself on
planted files.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("core", "apps", "agents")
ALLOWED = {"agents/base.py"}


def find_complete_calls(root: Path) -> list[str]:
    found = []
    for top in SCANNED:
        for path in sorted((root / top).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel in ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "complete":
                    found.append(f"{rel}:{node.lineno}")
                elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
                      and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                      and node.args[1].value == "complete"):
                    found.append(f"{rel}:{node.lineno}")
    return found


def test_complete_is_called_only_in_agents_base():
    assert find_complete_calls(REPO_ROOT) == [], "a model call outside agents/base.Agent.run()"


def test_agents_base_does_call_complete():
    """Otherwise the check above would pass with nothing to protect."""
    tree = ast.parse((REPO_ROOT / "agents" / "base.py").read_text(encoding="utf-8"))
    assert any(isinstance(n, ast.Attribute) and n.attr == "complete" for n in ast.walk(tree))


PLANTED = {
    "a step": ("core/steps/s1_scope.py", "def f(llm):\n    return llm.complete(prompt_id='x')\n"),
    "an endpoint": ("apps/api/routers/consumer.py", "def f(p):\n    p.complete()\n"),
    "another agent": ("agents/intent.py", "class A:\n    def go(self):\n        self.provider.complete()\n"),
    "a bound method": ("agents/intent.py", "def f(p):\n    call = p.complete\n    return call()\n"),
    "getattr": ("core/x.py", "def f(p):\n    return getattr(p, 'complete')()\n"),
}


@pytest.mark.parametrize("case", sorted(PLANTED))
def test_the_checker_catches_a_planted_call(tmp_path, case):
    for top in SCANNED:
        (tmp_path / top).mkdir()
    (tmp_path / "agents" / "base.py").write_text("def run(p):\n    p.complete()\n", encoding="utf-8")
    rel, source = PLANTED[case]
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    assert [hit.split(":")[0] for hit in find_complete_calls(tmp_path)] == [rel]
