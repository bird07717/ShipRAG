#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="${project_root}/.venv/bin/python"

"${project_root}/scripts/check.sh"
npm --prefix "${project_root}/frontend" audit \
  --registry=https://registry.npmjs.org --audit-level=high
docker compose --file "${project_root}/compose.production.yaml" config --quiet
"${project_root}/scripts/smoke-production.sh"
docker compose --file "${project_root}/compose.yaml" up \
  --detach --wait postgres redis minio
docker compose --file "${project_root}/compose.yaml" run --rm minio-init
(
  cd "${project_root}/backend"
  "${python}" -m alembic upgrade head
  "${python}" -m app.release.acceptance \
    --port "${M7_ACCEPTANCE_PORT:-18007}" \
    --load-requests "${M7_LOAD_REQUESTS:-200}" \
    --load-concurrency "${M7_LOAD_CONCURRENCY:-32}" \
    --rag-requests "${M7_RAG_REQUESTS:-50}" \
    --p95-limit-ms "${M7_P95_LIMIT_MS:-3000}" \
    --recovery-timeout "${M7_RECOVERY_TIMEOUT_SECONDS:-90}" \
    --task-timeout "${M7_TASK_TIMEOUT_SECONDS:-180}"
)
