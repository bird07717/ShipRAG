#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
online_args=()

if [[ "${1:-}" == "--allow-blocked" ]]; then
  online_args+=("--allow-blocked")
  shift
fi
if (( $# > 0 )); then
  echo "Usage: $0 [--allow-blocked]" >&2
  exit 64
fi

(
  cd "${project_root}/backend"
  "${project_root}/.venv/bin/python" -m pytest -q --no-cov \
    tests/test_m0_docx.py tests/test_m0_online.py
  "${project_root}/.venv/bin/python" -m app.m0.storage_probe
)

"${project_root}/scripts/check-m0-bm25.sh"

(
  cd "${project_root}/backend"
  "${project_root}/.venv/bin/python" -m app.m0.cli \
    --output "${project_root}/docs/evidence/m0-online-report.json" \
    "${online_args[@]}"
)
