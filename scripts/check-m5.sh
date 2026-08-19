#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="${project_root}/.venv/bin/python"

embedding_provider="${M5_SMOKE_EMBEDDING_PROVIDER:-fake}"
rerank_provider="${M5_SMOKE_RERANK_PROVIDER:-fake}"
llm_provider="${M5_SMOKE_LLM_PROVIDER:-fake}"

cd "${project_root}/backend"
"${python}" -m alembic upgrade head
"${python}" -m pytest tests/test_m5_retrieval.py --no-cov
"${python}" -m app.rag.smoke \
  --embedding-provider "${embedding_provider}" \
  --rerank-provider "${rerank_provider}" \
  --llm-provider "${llm_provider}"
