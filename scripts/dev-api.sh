#!/usr/bin/env bash
# API 开发进程：通过共享迁移门槛后，以热重载模式启动 uvicorn。
# 由 Procfile 的 api 条目调用（honcho start），也可单独执行。
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
bash "${root}/scripts/dev-migrate.sh"

cd "${root}/backend"
exec "../.venv/bin/python" -m uvicorn app.main:app \
  --host 127.0.0.1 --port 18000 --reload
