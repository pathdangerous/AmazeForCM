#!/usr/bin/env bash
# Replit baseline: standalone Python is the approved alternative to .venv.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/.runtime/baseline-packages:$PWD"
python="$PWD/.runtime/python/cpython-3.14-linux-x86_64-gnu/bin/python3.14"
[[ "$("$python" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')" == "3.14" ]]

pids=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${pids[@]}"; do kill -- "-$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM
LLM_PROVIDER="${DEV_LLM_PROVIDER:-mock}" setsid "$python" -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 &
pids+=("$!")
(cd apps/backend-ui && exec setsid npm run dev) &
pids+=("$!")
(cd apps/consumer-ui && exec setsid npm run dev) &
pids+=("$!")
setsid node scripts/preview-proxy.mjs &
pids+=("$!")
wait -n "${pids[@]}"
exit 1
