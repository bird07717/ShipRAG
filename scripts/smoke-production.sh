#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_name="enterprise-rag-m7-smoke"
http_port="${M7_PRODUCTION_SMOKE_PORT:-18008}"
compose_file="${project_root}/compose.production.yaml"
backend_image="${BACKEND_IMAGE:-enterprise-rag-backend:1.0.0}"

cleanup() {
  docker compose \
    --project-name "${project_name}" \
    --file "${compose_file}" \
    down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

export APP_HTTP_PORT="${http_port}"
export APP_HTTP_BIND_ADDRESS="127.0.0.1"
export SERVICE_TOKEN="m7-smoke-service-token"
export ASSISTANT_SERVICE_TOKEN="m7-smoke-assistant-token-0123456789"
export POSTGRES_PASSWORD="m7-smoke-postgres-password"
export REDIS_PASSWORD="m7-smoke-redis-password"
export MINIO_ROOT_USER="m7-smoke-minio"
export MINIO_ROOT_PASSWORD="m7-smoke-minio-password"
export M2_EMBEDDING_PROVIDER="fake"
# Production settings reject the fake LLM provider.  This smoke check does not
# issue a chat request, so auto avoids external model calls while preserving
# the same production validation path.
export M3_LLM_PROVIDER="auto"
export M4_OCR_PROVIDER="fake"
export M4_VISION_PROVIDER="fake"
export M5_RERANK_PROVIDER="fake"

docker compose \
  --project-name "${project_name}" \
  --file "${compose_file}" \
  config --quiet

docker compose \
  --project-name "${project_name}" \
  --file "${compose_file}" \
  up --detach --build --wait --wait-timeout 240

docker run --rm "${backend_image}" python -m pip check
docker run --rm "${backend_image}" python -c \
  "import importlib.util; assert importlib.util.find_spec('pytest') is None"

"${project_root}/.venv/bin/python" - "${http_port}" <<'PY'
import json
import sys
import urllib.request
from urllib.error import HTTPError

port = int(sys.argv[1])
with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/ready", timeout=10) as response:
    readiness = json.load(response)
with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
    html = response.read().decode("utf-8")

assistant_request = urllib.request.Request(
    f"http://127.0.0.1:{port}/api/v1/assistant/knowledge-bases",
    headers={
        "Authorization": "Bearer m7-smoke-assistant-token-0123456789",
        "X-External-User-ID": "m7-production-smoke",
    },
)
with urllib.request.urlopen(assistant_request, timeout=10) as response:
    assistant_payload = json.load(response)

try:
    urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/v1/assistant/knowledge-bases", timeout=10
    )
except HTTPError as error:
    if error.code != 401:
        raise SystemExit(f"unauthenticated Assistant request returned {error.code}, expected 401")
else:
    raise SystemExit("unauthenticated Assistant request unexpectedly succeeded")

if readiness.get("status") != "ready":
    raise SystemExit("production readiness failed")
if '<div id="app"></div>' not in html:
    raise SystemExit("production frontend entry did not load")
if not isinstance(assistant_payload.get("items"), list):
    raise SystemExit("production Assistant knowledge-base endpoint failed")
print(
    json.dumps(
        {
            "status": "passed",
            "readiness": "ready",
            "frontend": "served",
            "assistant": "reachable",
        }
    )
)
PY
