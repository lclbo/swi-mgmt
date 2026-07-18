#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API_PID=""

cleanup() {
  if [[ -n "${API_PID}" ]] && kill -0 "${API_PID}" 2>/dev/null; then
    kill "${API_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if ! command -v swi-mgmt-api >/dev/null 2>&1; then
  echo "swi-mgmt-api not found; install with: pip install -e ."
  exit 1
fi

if curl -sf "http://127.0.0.1:18742/api/health" >/dev/null 2>&1; then
  echo "reusing existing API on 127.0.0.1:18742"
else
  # Free a dead/orphaned listener so bind does not fail with Errno 48.
  if command -v lsof >/dev/null 2>&1; then
    while read -r pid; do
      [[ -n "${pid}" ]] || continue
      kill "${pid}" 2>/dev/null || true
    done < <(lsof -nP -tiTCP:18742 -sTCP:LISTEN 2>/dev/null || true)
    sleep 0.2
  fi

  swi-mgmt-api --host 127.0.0.1 --port 18742 &
  API_PID=$!

  for _ in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:18742/api/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done
fi

npm run dev --workspace=frontend
