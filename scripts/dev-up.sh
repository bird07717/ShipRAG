#!/usr/bin/env bash
# 一键启动开发环境：基础设施、Alembic 门槛和 Honcho 全部进程。
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "${root}"

# `minio-init` is a successful one-shot container. Including it in `up --wait`
# makes Compose treat its normal exit as a stopped service and prevents Honcho
# from starting, so wait only for the long-running infrastructure first.
docker compose up -d --wait postgres redis minio
docker compose run --rm --no-deps minio-init
exec "${root}/.venv/bin/honcho" start "$@"
