#!/usr/bin/env bash
# 等待 docker compose 基础设施端口就绪。
# 用法: scripts/wait-infra.sh
# 说明: `docker compose up -d --wait` 已做 healthcheck 门控；本脚本是兜底，
#       覆盖忘记带 --wait、或基础设施刚重启的场景。
set -euo pipefail

wait_port() {
  local name="$1" port="$2"
  local attempt
  for attempt in $(seq 1 60); do
    if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
      echo "infra ready: ${name} (127.0.0.1:${port})"
      return 0
    fi
    sleep 0.5
  done
  echo "等待 ${name} (127.0.0.1:${port}) 超时——请先运行: docker compose up -d --wait" >&2
  return 1
}

wait_port postgres 15432
wait_port redis 16379
wait_port minio 19000
