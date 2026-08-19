#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="${project_root}/.venv/bin/python"

"${project_root}/scripts/check-m5.sh"
(
  cd "${project_root}/backend"
  "${python}" -m alembic upgrade head
  "${python}" -m app.admin.smoke
)
npm --prefix "${project_root}/frontend" run lint
npm --prefix "${project_root}/frontend" run type-check
npm --prefix "${project_root}/frontend" run test:coverage
npm --prefix "${project_root}/frontend" run build
