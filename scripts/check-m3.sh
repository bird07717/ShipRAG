#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="${project_root}/.venv/bin/python"

(
  cd "${project_root}/backend"
  "${python}" -m alembic upgrade head
  "${python}" -m pytest tests/test_m3_rag.py --no-cov
  "${python}" -m app.rag.smoke \
    --embedding-provider "${M3_SMOKE_EMBEDDING_PROVIDER:-fake}" \
    --llm-provider "${M3_SMOKE_LLM_PROVIDER:-fake}"
)
