#!/usr/bin/env bash
# 一键启动开发环境：基础设施、Alembic 门槛和 Honcho 全部进程。
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "${root}"

docker compose up -d --wait
exec "${root}/.venv/bin/honcho" start "$@"
