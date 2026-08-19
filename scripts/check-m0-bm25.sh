#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="${project_root}/compose.m0-bm25.yaml"
temporary_dir="$(mktemp -d)"

cleanup() {
  docker compose -f "${compose_file}" down >/dev/null 2>&1 || true
  rm -rf "${temporary_dir}"
}
trap cleanup EXIT

docker compose -f "${compose_file}" up -d

container_id="$(docker compose -f "${compose_file}" ps -q postgres-bm25)"
for _ in {1..40}; do
  health_status="$(docker inspect --format '{{.State.Health.Status}}' "${container_id}")"
  if [[ "${health_status}" == "healthy" ]]; then
    break
  fi
  sleep 1
done

if [[ "${health_status}" != "healthy" ]]; then
  docker compose -f "${compose_file}" logs --no-color postgres-bm25 >&2
  echo "M0 BM25 database did not become healthy." >&2
  exit 1
fi

docker compose -f "${compose_file}" exec -T postgres-bm25 \
  psql -U m0 -d m0_bm25 -f /dev/stdin < "${project_root}/scripts/m0-bm25-smoke.sql"

docker compose -f "${compose_file}" exec -T postgres-bm25 \
  pg_dump -U m0 -d m0_bm25 -Fc -t m0_bm25_documents > "${temporary_dir}/bm25.dump"
docker compose -f "${compose_file}" exec -T postgres-bm25 \
  createdb -U m0 m0_bm25_restore
docker compose -f "${compose_file}" exec -T postgres-bm25 \
  psql -U m0 -d m0_bm25_restore -v ON_ERROR_STOP=1 -c 'CREATE EXTENSION IF NOT EXISTS pg_search CASCADE'
docker compose -f "${compose_file}" exec -T postgres-bm25 \
  pg_restore -U m0 -d m0_bm25_restore --exit-on-error < "${temporary_dir}/bm25.dump"
docker compose -f "${compose_file}" exec -T postgres-bm25 \
  psql -U m0 -d m0_bm25_restore -v ON_ERROR_STOP=1 -c \
  "SELECT id FROM m0_bm25_documents WHERE content ||| '数据库 默认 端口 3306' ORDER BY pdb.score(id) DESC LIMIT 1"

echo "M0 BM25 smoke and backup/restore passed."
