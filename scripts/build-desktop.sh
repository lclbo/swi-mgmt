#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Leftover incomplete DMGs from a previous failed bundle can confuse hdiutil.
rm -f "${ROOT}"/src-tauri/target/release/bundle/macos/rw.*.dmg 2>/dev/null || true

bash "${ROOT}/scripts/build-api.sh"

# Refresh .app / favicon assets from assets/app-icon.svg when ImageMagick is available.
if command -v magick >/dev/null 2>&1 || command -v convert >/dev/null 2>&1; then
  python3 "${ROOT}/scripts/make_icons.py"
fi

# Default: .app only (reliable). For a DMG as well:
#   TAURI_BUNDLES=app,dmg npm run tauri:build
BUNDLES="${TAURI_BUNDLES:-app}"
npm run tauri -- build --bundles "${BUNDLES}"

APP="${ROOT}/src-tauri/target/release/bundle/macos/SWI-MGMT.app"
if [[ -d "${APP}" ]]; then
  echo "Desktop app: ${APP}"
fi
DMG_DIR="${ROOT}/src-tauri/target/release/bundle/dmg"
if compgen -G "${DMG_DIR}/SWI-MGMT_*.dmg" > /dev/null; then
  ls -1 "${DMG_DIR}"/SWI-MGMT_*.dmg
fi
