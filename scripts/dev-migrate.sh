#!/usr/bin/env bash
# 开发态启动门槛：基础设施就绪后串行执行 Alembic 迁移。
# API 与两个 worker 都会调用本脚本；flock 防止它们并发修改 alembic_version。
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
lock_file="${TMPDIR:-/tmp}/shipdemo-alembic-upgrade.lock"

bash "${root}/scripts/wait-infra.sh"
flock -x "${lock_file}" "${root}/.venv/bin/alembic" \
  -c "${root}/backend/alembic.ini" upgrade head
