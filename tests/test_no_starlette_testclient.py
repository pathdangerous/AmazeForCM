"""No test may use Starlette's (or FastAPI's) TestClient.

Importing starlette.testclient warns "Using `httpx` with `starlette.testclient` is deprecated;
install `httpx2` instead." (Starlette 1.10). The project does not adopt httpx2, so API tests call the
app in-process with httpx.AsyncClient and httpx.ASGITransport instead.
"""

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
BANNED = {"starlette.testclient", "fastapi.testclient"}


def _imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_no_test_imports_the_starlette_test_client():
    offenders = {
        path.name: sorted(_imports(path) & BANNED)
        for path in sorted(TESTS.rglob("*.py"))
        if _imports(path) & BANNED
    }
    assert offenders == {}, f"use httpx.AsyncClient with httpx.ASGITransport instead: {offenders}"
