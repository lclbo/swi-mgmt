#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v pyinstaller >/dev/null 2>&1; then
  pip install pyinstaller
fi

TARGET="$(rustc -vV | awk '/host: / {print $2}')"
OUT_DIR="${ROOT}/src-tauri/binaries"
mkdir -p "${OUT_DIR}"

pyinstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name "swi-mgmt-api-${TARGET}" \
  --distpath "${OUT_DIR}" \
  --workpath "${ROOT}/build/pyinstaller" \
  --specpath "${ROOT}/build/pyinstaller" \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  --collect-all pysnmp \
  --hidden-import openpyxl \
  --collect-all openpyxl \
  "${ROOT}/scripts/api_entry.py"

# Tauri externalBin "binaries/swi-mgmt-api" resolves to
# binaries/swi-mgmt-api-<rustc-host-triple> at bundle time.
BINARY="${OUT_DIR}/swi-mgmt-api-${TARGET}"
if [[ ! -x "${BINARY}" ]]; then
  echo "error: expected sidecar ${BINARY}" >&2
  exit 1
fi

# macOS: ad-hoc sign with entitlements so Gatekeeper/library-validation allows
# PyInstaller's extracted Python.framework dylibs on first launch.
if [[ "$(uname -s)" == "Darwin" ]]; then
  ENTITLEMENTS="${ROOT}/src-tauri/Entitlements.plist"
  if [[ -f "${ENTITLEMENTS}" ]]; then
    codesign --force --options runtime --entitlements "${ENTITLEMENTS}" --sign - "${BINARY}"
    echo "Signed sidecar (ad-hoc + entitlements): ${BINARY}"
  fi
fi

echo "Built sidecar: ${BINARY}"
