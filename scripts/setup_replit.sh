#!/usr/bin/env bash
# Rebuild the approved standalone runtime; never create a virtualenv.
# Move an existing .runtime aside first, or set RUNTIME_ROOT to a fresh directory.
set -euo pipefail
cd "$(dirname "$0")/.."
runtime="${RUNTIME_ROOT:-$PWD/.runtime}"
if [[ -d "$runtime" && -n "$(ls -A "$runtime")" ]]; then
  echo "Runtime target is not empty: move it aside or choose a fresh RUNTIME_ROOT." >&2
  exit 1
fi
command -v uv >/dev/null || { echo "uv is required to install standalone Python." >&2; exit 1; }
mkdir -p "$runtime"
runtime="$(cd "$runtime" && pwd)"
UV_PYTHON_INSTALL_DIR="$runtime/python" uv python install 3.14.10
python="$runtime/python/cpython-3.14.10-linux-x86_64-gnu/bin/python3.14"
[[ "$("$python" -c 'import platform; print(platform.python_version())')" == "3.14.10" ]]
ln -s cpython-3.14.10-linux-x86_64-gnu "$runtime/python/cpython-3.14-linux-x86_64-gnu"
# Run bundled pip directly: uv marks the interpreter externally managed.
# --target keeps dependencies outside that interpreter without overriding PEP 668.
pip_wheels=("$runtime/python/cpython-3.14.10-linux-x86_64-gnu/lib/python3.14/ensurepip/_bundled"/pip-*.whl)
PYTHONPATH="${pip_wheels[0]}" "$python" -m pip install --target "$runtime/baseline-packages" -r requirements-dev.txt
PYTHONPATH="$runtime/baseline-packages:$PWD" "$python" -c \
  'import pytest, fastapi, sqlalchemy, psycopg, duckdb; print("Standalone Python 3.14 runtime ready")'
