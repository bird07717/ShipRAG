#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="${project_root}/.venv/bin/python"

"${python}" -m ruff check "${project_root}/backend"
"${python}" -m ruff format --check "${project_root}/backend"
(
  cd "${project_root}/backend"
  "${python}" -m mypy app
  "${python}" -m pytest --cov-report= --cov-fail-under=0
  "${python}" -m coverage run --append -m app.ingestion.smoke --provider fake
  "${python}" -m coverage run --append -m app.rag.smoke \
    --embedding-provider fake --llm-provider fake
  "${python}" -m coverage run --append -m app.admin.smoke
  "${python}" -m coverage report --fail-under=80
)
npm --prefix "${project_root}/frontend" run lint
npm --prefix "${project_root}/frontend" run type-check
npm --prefix "${project_root}/frontend" run test:coverage
npm --prefix "${project_root}/frontend" run build
