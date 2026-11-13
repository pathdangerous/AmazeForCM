"""Rule 2 of CLAUDE.md: no model call in the run engine or in entitlement decisions.

core/engine and core/entitlement must not import `agents` or `ports.llm`, directly or
through any other module. The check is static (it parses source; it runs nothing).
Limit: an import built from a string at run time (importlib.import_module("...")) is not seen.
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED_PACKAGES = ("core", "agents", "ports", "apps")
BOUNDARY_PACKAGES = ("core.engine", "core.entitlement")
FORBIDDEN = ("agents", "ports.llm")


# ---------------------------------------------------------------- checker


def _module_name(root: Path, path: Path) -> tuple[str, bool]:
    parts = list(path.relative_to(root).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), is_package


def _with_parents(name: str) -> list[str]:
    """Importing a.b.c also runs a/__init__ and a/b/__init__."""
    parts = name.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def _imports_of(tree: ast.AST, module: str, is_package: bool) -> set[str]:
    package = module if is_package else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.update(_with_parents(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".") if package else []
                drop = node.level - 1
                if drop > len(base_parts):
                    continue  # relative import beyond the top: not resolvable, Python would fail too
                base_parts = base_parts[: len(base_parts) - drop]
                if node.module:
                    base_parts += node.module.split(".")
                base = ".".join(base_parts)
            else:
                base = node.module or ""
            if not base:
                continue
            found.update(_with_parents(base))
            for alias in node.names:
                if alias.name != "*":
                    found.add(f"{base}.{alias.name}")  # may be a submodule: `from ports import llm`
    return found


def build_import_graph(root: Path, packages: tuple[str, ...]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for top in packages:
        for path in sorted((root / top).rglob("*.py")):
            module, is_package = _module_name(root, path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            edges = _imports_of(tree, module, is_package)
            edges.update(p for p in _with_parents(module) if p != module)  # own parent packages run first
            graph[module] = edges
    return graph


def _is_forbidden(name: str) -> bool:
    return any(name == f or name.startswith(f + ".") for f in FORBIDDEN)


def _in_boundary(name: str) -> bool:
    return any(name == b or name.startswith(b + ".") for b in BOUNDARY_PACKAGES)


def find_violations(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return one import chain per boundary module that reaches a forbidden module."""
    violations = []
    for start in sorted(m for m in graph if _in_boundary(m)):
        came_from: dict[str, str | None] = {start: None}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            if _is_forbidden(current):
                chain = [current]
                while came_from[chain[-1]] is not None:
                    chain.append(came_from[chain[-1]])
                violations.append(list(reversed(chain)))
                break
            for nxt in sorted(graph.get(current, ())):
                if nxt not in came_from:
                    came_from[nxt] = current
                    queue.append(nxt)
    return violations


def _format(violations: list[list[str]]) -> str:
    return "\n".join("  " + " -> ".join(chain) for chain in violations)


# ---------------------------------------------------------------- the real check


def test_boundary_packages_exist_and_are_scanned():
    graph = build_import_graph(REPO_ROOT, SCANNED_PACKAGES)
    for package in BOUNDARY_PACKAGES:
        assert package in graph, f"{package} not found; the boundary test would check nothing"


def test_engine_and_entitlement_do_not_reach_agents_or_llm():
    violations = find_violations(build_import_graph(REPO_ROOT, SCANNED_PACKAGES))
    assert not violations, "Forbidden import path(s) found (CLAUDE.md rule 2):\n" + _format(violations)


# ---------------------------------------------------------------- the checker tests itself

BASE_TREE = {
    "core/__init__.py": "",
    "core/engine/__init__.py": "",
    "core/entitlement/__init__.py": "",
    "core/helpers.py": "",
    "agents/__init__.py": "",
    "agents/mapping.py": "",
    "ports/__init__.py": "",
    "ports/llm/__init__.py": "",
    "ports/llm/mock.py": "",
    "ports/storage/__init__.py": "",
    "apps/__init__.py": "",
}

PLANTED = {
    "direct import": {"core/engine/run.py": "import agents.mapping\n"},
    "from ports import llm": {"core/engine/run.py": "from ports import llm\n"},
    "from ports.llm.mock import X": {"core/entitlement/decide.py": "from ports.llm.mock import MockProvider\n"},
    "relative import": {"core/engine/run.py": "from ...agents import mapping\n"},
    "through a helper": {
        "core/engine/run.py": "from core import helpers\n",
        "core/helpers.py": "import ports.llm\n",
    },
    "through a parent package": {"core/__init__.py": "import agents\n"},
    "inside a function": {"core/entitlement/decide.py": "def f():\n    import agents\n"},
}


def _write_tree(root: Path, overrides: dict[str, str]) -> None:
    for rel, text in {**BASE_TREE, **overrides}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("case", sorted(PLANTED))
def test_checker_catches_planted_violation(tmp_path, case):
    _write_tree(tmp_path, PLANTED[case])
    violations = find_violations(build_import_graph(tmp_path, SCANNED_PACKAGES))
    assert violations, f"planted violation not caught: {case}"
    assert all(_is_forbidden(chain[-1]) for chain in violations)


def test_checker_allows_other_ports(tmp_path):
    _write_tree(
        tmp_path,
        {
            "core/engine/run.py": "import ports.storage\nfrom core import helpers\nimport json\n",
            "core/helpers.py": "from ports.storage import x\n",
        },
    )
    assert find_violations(build_import_graph(tmp_path, SCANNED_PACKAGES)) == []


def test_checker_reports_the_chain(tmp_path):
    _write_tree(tmp_path, PLANTED["through a helper"])
    violations = find_violations(build_import_graph(tmp_path, SCANNED_PACKAGES))
    assert ["core.engine.run", "core.helpers", "ports.llm"] in violations
