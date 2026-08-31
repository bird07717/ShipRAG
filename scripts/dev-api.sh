#!/usr/bin/env bash
# API 开发进程：等基础设施就绪 -> 执行迁移 -> 以热重载模式启动 uvicorn。
# 由 Procfile 的 api 条目调用（honcho start），也可单独执行。
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
bash "${root}/scripts/wait-infra.sh"

cd "${root}/backend"
"../.venv/bin/alembic" upgrade head
exec "../.venv/bin/python" -m uvicorn app.main:app \
  --host 127.0.0.1 --port 18000 --reload
