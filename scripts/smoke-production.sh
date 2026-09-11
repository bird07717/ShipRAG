#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_name="enterprise-rag-m7-smoke"
http_port="${M7_PRODUCTION_SMOKE_PORT:-18008}"
compose_file="${project_root}/compose.production.yaml"
backend_image="${BACKEND_IMAGE:-enterprise-rag-backend:1.0.1}"
dns_blocker="${project_name}-dns-ip-blocker"

cleanup() {
  docker rm --force "${dns_blocker}" >/dev/null 2>&1 || true
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
# Fixed test-only AES-256 key; production receives its key from a Secret.
export MODEL_SECRET_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
export MODEL_SECRET_KEY_VERSION="1"
# Production settings reject fake providers. This smoke check does not issue
# model-backed ingestion or chat requests, so auto preserves the production
# validation path without making external model calls.
export M2_EMBEDDING_PROVIDER="auto"
export M3_LLM_PROVIDER="auto"
export M4_OCR_PROVIDER="fake"
export M4_VISION_PROVIDER="fake"
export M5_RERANK_PROVIDER="auto"

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

create_knowledge_request = urllib.request.Request(
    f"http://127.0.0.1:{port}/api/v1/knowledge-bases",
    data=json.dumps({"name": "m7-private-smoke", "description": "publication boundary"}).encode(),
    headers={
        "Authorization": "Bearer m7-smoke-service-token",
        "Content-Type": "application/json",
    },
    method="POST",
)
with urllib.request.urlopen(create_knowledge_request, timeout=10) as response:
    private_knowledge = json.load(response)

with urllib.request.urlopen(assistant_request, timeout=10) as response:
    assistant_after_create = json.load(response)

private_knowledge_id = private_knowledge["id"]
offline_welcome_request = urllib.request.Request(
    f"http://127.0.0.1:{port}/api/v1/assistant/knowledge-bases/{private_knowledge_id}/welcome",
    headers={
        "Authorization": "Bearer m7-smoke-assistant-token-0123456789",
        "X-External-User-ID": "m7-production-smoke",
    },
)
try:
    urllib.request.urlopen(offline_welcome_request, timeout=10)
except HTTPError as error:
    offline_welcome_payload = json.load(error)
    if error.code != 404 or offline_welcome_payload.get("error", {}).get("code") != (
        "KNOWLEDGE_BASE_NOT_FOUND"
    ):
        raise SystemExit(
            f"offline Assistant welcome returned unexpected error: "
            f"{error.code} {offline_welcome_payload}"
        )
else:
    raise SystemExit("offline knowledge base unexpectedly exposed its welcome")

preflight_request = urllib.request.Request(
    f"http://127.0.0.1:{port}/api/v1/assistant/chat/stream",
    data=json.dumps(
        {
            "knowledge_id": "00000000-0000-0000-0000-000000000001",
            "conversation_id": "00000000-0000-0000-0000-000000000002",
            "question": "preflight contract check",
        }
    ).encode(),
    headers={
        "Authorization": "Bearer m7-smoke-assistant-token-0123456789",
        "X-External-User-ID": "m7-production-smoke",
        "Idempotency-Key": "m7-preflight-contract-check",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    },
    method="POST",
)
try:
    urllib.request.urlopen(preflight_request, timeout=10)
except HTTPError as error:
    preflight_payload = json.load(error)
    if error.code != 404 or preflight_payload.get("error", {}).get("code") != (
        "CONVERSATION_NOT_FOUND"
    ):
        raise SystemExit(
            f"Assistant preflight returned unexpected error: {error.code} {preflight_payload}"
        )
    if not error.headers.get_content_type() == "application/json":
        raise SystemExit("Assistant preflight error did not use JSON")
else:
    raise SystemExit("Assistant preflight error unexpectedly established SSE")

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
if private_knowledge.get("assistant_online") is not False:
    raise SystemExit("new knowledge base did not default to Assistant offline")
if any(
    item.get("knowledge_id") == private_knowledge_id
    for item in assistant_after_create.get("items", [])
):
    raise SystemExit("offline knowledge base leaked into the Assistant catalog")
print(
    json.dumps(
        {
            "status": "passed",
            "readiness": "ready",
            "frontend": "served",
            "assistant": "reachable",
            "assistant_preflight": "http-json",
            "assistant_publication_default": "private",
        }
    )
)
PY

# Reproduce a backend replacement that changes its container IP while keeping
# the frontend/Nginx container alive. The proxy must recover through Docker DNS
# without requiring an Nginx restart.
backend_container="$(
  docker compose \
    --project-name "${project_name}" \
    --file "${compose_file}" \
    ps --quiet backend
)"
backend_ip_before="$(
  docker inspect \
    --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
    "${backend_container}"
)"
compose_network="$(
  docker inspect \
    --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' \
    "${backend_container}"
)"

docker compose \
  --project-name "${project_name}" \
  --file "${compose_file}" \
  stop backend
docker compose \
  --project-name "${project_name}" \
  --file "${compose_file}" \
  rm --force backend
docker run \
  --detach \
  --rm \
  --name "${dns_blocker}" \
  --network "${compose_network}" \
  "${backend_image}" \
  python -c "import time; time.sleep(120)" >/dev/null
docker compose \
  --project-name "${project_name}" \
  --file "${compose_file}" \
  up --detach --no-deps --wait --wait-timeout 120 backend

backend_container="$(
  docker compose \
    --project-name "${project_name}" \
    --file "${compose_file}" \
    ps --quiet backend
)"
backend_ip_after="$(
  docker inspect \
    --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
    "${backend_container}"
)"
if [[ "${backend_ip_before}" == "${backend_ip_after}" ]]; then
  echo "backend IP did not change; dynamic DNS regression was not exercised" >&2
  exit 1
fi

proxy_recovered="false"
for _ in $(seq 1 30); do
  if curl --fail --silent \
    --max-time 5 \
    "http://127.0.0.1:${http_port}/health/ready" >/dev/null; then
    proxy_recovered="true"
    break
  fi
  sleep 1
done
if [[ "${proxy_recovered}" != "true" ]]; then
  echo "frontend proxy did not recover after backend IP changed" >&2
  exit 1
fi

docker rm --force "${dns_blocker}" >/dev/null
echo "production nginx dynamic DNS: passed (${backend_ip_before} -> ${backend_ip_after})"
