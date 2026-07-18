#!/usr/bin/env bash
# Placeholder sidecar for Tauri dev builds (replaced by PyInstaller for release).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$(rustc -vV 2>/dev/null | awk '/host: / {print $2}')"
if [[ -z "${TARGET}" ]]; then
  echo "rustc not found; install Rust for Tauri builds"
  exit 1
fi

OUT_DIR="${ROOT}/src-tauri/binaries"
OUT="${OUT_DIR}/swi-mgmt-api-${TARGET}"
mkdir -p "${OUT_DIR}"

cat > "${OUT}" << EOF
#!/usr/bin/env bash
ROOT="\$(cd "\$(dirname "\$0")/../../.." && pwd)"
PY="\${ROOT}/.venv/bin/python"
if [[ ! -x "\${PY}" ]]; then
  PY="\$(command -v python3)"
fi
exec "\${PY}" -m swi_mgmt.api.server "\$@"
EOF
chmod +x "${OUT}"
echo "Dev sidecar: ${OUT}"
