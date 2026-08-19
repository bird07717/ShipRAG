#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="${project_root}/.venv/bin/python"

embedding_provider="${M4_SMOKE_EMBEDDING_PROVIDER:-fake}"
ocr_provider="${M4_SMOKE_OCR_PROVIDER:-fake}"
vision_provider="${M4_SMOKE_VISION_PROVIDER:-fake}"

cd "${project_root}/backend"
"${python}" -m alembic upgrade head
"${python}" -m pytest tests/test_m4_multimodal.py --no-cov
"${python}" -m app.ingestion.smoke \
  --provider "${embedding_provider}" \
  --ocr-provider "${ocr_provider}" \
  --vision-provider "${vision_provider}"
