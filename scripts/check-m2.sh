#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="${project_root}/.venv/bin/python"
provider="${M2_SMOKE_PROVIDER:-fake}"

(
  cd "${project_root}/backend"
  "${project_root}/.venv/bin/alembic" upgrade head
  "${python}" -m pytest -q --no-cov tests/test_m2_ingestion.py
  "${python}" -m app.ingestion.smoke --provider "${provider}"
)
