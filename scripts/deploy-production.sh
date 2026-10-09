#!/usr/bin/env bash
# One-command production update: pull → build (with build identity) → migrate
# → replace application containers → verify the running commit.
#
# Usage: bash scripts/deploy-production.sh [options]
#   --frontend-only     build and replace only the frontend (no migration)
#   --backend-only      build and replace backend + both workers (with migration)
#   --skip-pull         deploy the current checkout without `git pull`
#   --env-file FILE     compose env file (default: the root .env compose reads)
#   --force             replace workers even if background tasks are running
#   --yes               do not ask for confirmation
#   --dry-run           run read-only checks and print the mutating commands
#   --rollback [TAG]    re-deploy a backup made by an earlier run (default:
#                       the newest tag shared by target images); no migration
#
# A failed step stops the script; containers are only replaced after a
# successful build and migration, so the previous version keeps running.
set -euo pipefail

project_root="${DEPLOY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "${project_root}"

compose_file="compose.production.yaml"
env_args=()
target="all"
skip_pull=false
force=false
assume_yes=false
dry_run=false
rollback=false
rollback_tag=""
keep_backups=3

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}" 2>/dev/null; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --frontend-only) target="frontend" ;;
    --backend-only) target="backend" ;;
    --skip-pull) skip_pull=true ;;
    --env-file) env_args=(--env-file "${2:?--env-file needs a path}"); shift ;;
    --force) force=true ;;
    --yes|-y) assume_yes=true ;;
    --dry-run) dry_run=true ;;
    --rollback)
      rollback=true
      if [[ -n "${2:-}" && "${2}" != --* ]]; then rollback_tag="$2"; shift; fi
      ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
# Mutating commands go through run(); --dry-run only prints them.
run() {
  if [[ "${dry_run}" == true ]]; then
    printf '    [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}
compose() { docker compose "${env_args[@]}" -f "${compose_file}" "$@"; }
# exec/run must never read this script's stdin: when piped (bash -s) they would
# swallow the rest of the script, and on a terminal they would eat keystrokes.
compose_exec() { compose exec -T "$@" </dev/null; }

case "${target}" in
  frontend) build_services=(frontend); app_services=(frontend); needs_migration=false ;;
  backend) build_services=(backend); app_services=(backend worker-index worker-maintenance); needs_migration=true ;;
  *) build_services=(backend frontend); app_services=(backend worker-index worker-maintenance frontend); needs_migration=true ;;
esac
replaces_workers=false
[[ " ${app_services[*]} " == *" worker-index "* ]] && replaces_workers=true

image_for() {
  compose config --format json \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"][sys.argv[1]]["image"])' "$1"
}

active_task_count() {
  # Tasks with progress in the last 30 minutes; long-silent orphans (e.g. of a
  # deleted knowledge base) do not block a deploy.
  compose_exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "SELECT count(*) FROM task_record WHERE status IN ('"'"'QUEUED'"'"','"'"'RUNNING'"'"') AND updated_at > now() - interval '"'"'30 minutes'"'"'"'
}

check_background_tasks() {
  step "检查后台任务"
  if [[ "${replaces_workers}" != true ]]; then
    info "不替换 Worker，跳过"
    return 0
  fi
  local active
  active="$(active_task_count | tr -d '[:space:]')" || fail "无法读取后台任务状态"
  [[ "${active}" =~ ^[0-9]+$ ]] || fail "无法读取后台任务状态：${active}"
  if [[ "${active}" -gt 0 && "${force}" != true ]]; then
    fail "有 ${active} 个后台任务近 30 分钟内仍在进行，替换 Worker 会中断它们；等待完成或加 --force"
  fi
  info "进行中任务：${active}"
  if [[ "${active}" -gt 0 ]]; then info "--force 已启用，进行中任务可能被中断"; fi
}

confirm() {
  [[ "${assume_yes}" == true || "${dry_run}" == true ]] && return 0
  printf '\n%s [y/N] ' "$1"
  read -r answer
  [[ "${answer}" == "y" || "${answer}" == "Y" ]] || fail "已取消，未做任何更改"
}

backend_image="$(image_for backend)"
frontend_image="$(image_for frontend)"

# ---------------------------------------------------------------- rollback
if [[ "${rollback}" == true ]]; then
  step "回滚准备"
  images=()
  [[ "${target}" != "frontend" ]] && images+=("${backend_image}")
  [[ "${target}" != "backend" ]] && images+=("${frontend_image}")
  if [[ -z "${rollback_tag}" ]]; then
    tags="$(docker images --format '{{.Tag}}' "${images[0]%:*}")" || fail "无法读取备份镜像列表"
    mapfile -t candidates < <(printf '%s\n' "$tags" | grep '^rollback-' | sort -ru || true)
    for candidate in "${candidates[@]}"; do
      available=true
      for image in "${images[@]}"; do
        if ! docker image inspect "${image%:*}:${candidate}" >/dev/null 2>&1; then
          available=false
          break
        fi
      done
      if [[ "${available}" == true ]]; then rollback_tag="$candidate"; break; fi
    done
  fi
  [[ -n "${rollback_tag}" ]] || fail "没有找到目标服务共有的 rollback-* 备份镜像"
  for image in "${images[@]}"; do
    docker image inspect "${image%:*}:${rollback_tag}" >/dev/null 2>&1 \
      || fail "缺少备份镜像 ${image%:*}:${rollback_tag}"
    info "${image%:*}:${rollback_tag} → ${image}"
  done
  info "替换服务：${app_services[*]}（不构建、不执行迁移；若备份版本早于已执行的迁移，请先确认兼容）"
  [[ "${target}" != "frontend" ]] && info "替换后端期间，管理台与 Assistant API 会短暂中断"
  confirm "确认回滚到 ${rollback_tag}？"
  check_background_tasks
  for image in "${images[@]}"; do run docker tag "${image%:*}:${rollback_tag}" "${image}"; done
  step "替换容器"
  run compose up -d --no-deps --force-recreate --wait --wait-timeout 240 "${app_services[@]}"
  step "回滚完成：${rollback_tag}"
  exit 0
fi

# ---------------------------------------------------------------- preflight
step "检查代码"
if [[ "${skip_pull}" == false ]]; then
  run git pull --ff-only
fi
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  git status --short | head -10
  fail "工作区有未提交改动；构建标记会是 dirty，请先处理后再部署"
fi
commit="$(git rev-parse HEAD)"
info "将部署 $(git log -1 --format='%h %s')"

check_background_tasks

info "构建：${build_services[*]}；迁移：${needs_migration}；替换：${app_services[*]}"
[[ "${target}" != "frontend" ]] && info "替换后端期间，管理台与 Assistant API 会短暂中断"
confirm "确认部署 ${commit:0:8}？"

# ---------------------------------------------------------------- backup
step "备份当前镜像（用于 --rollback）"
stamp="rollback-$(date +%Y%m%d%H%M%S)"
for service in "${build_services[@]}"; do
  image="$(image_for "${service}")"
  containers="$(compose ps --status running -q "$service")" || fail "无法读取 ${service} 运行容器"
  if [[ -z "${containers}" ]]; then
    info "${service} 无运行容器，跳过备份（本次不生成该服务回滚备份）"
    continue
  fi
  running_image=""
  while IFS= read -r container; do
    container_image="$(docker inspect --format '{{.Image}}' "$container")" || fail "无法读取 ${service} 容器镜像"
    [[ -n "${container_image}" ]] || fail "${service} 容器镜像 ID 为空"
    [[ -z "${running_image}" || "${running_image}" == "${container_image}" ]] \
      || fail "${service} 运行容器使用不同镜像，无法生成单一回滚备份"
    running_image="${container_image}"
  done <<< "${containers}"
  run docker tag "${running_image}" "${image%:*}:${stamp}"
  info "${service} 运行镜像 ${running_image} → ${image%:*}:${stamp}"
  # Keep only the most recent backups to bound disk usage.
  mapfile -t old_tags < <(docker images --format '{{.Tag}}' "${image%:*}" | grep '^rollback-' | sort -r | tail -n +"$((keep_backups + 1))" || true)
  for old in "${old_tags[@]}"; do
    [[ -n "${old}" ]] && run docker rmi "${image%:*}:${old}" >/dev/null
  done
done

# ---------------------------------------------------------------- build
step "构建镜像（写入构建标记）"
if ! run bash scripts/build-production.sh "${env_args[@]}" "${build_services[@]}"; then
  docker buildx history ls 2>/dev/null | head -4 || true
  fail "构建失败，旧版本仍在运行；可用 docker buildx history logs <ID> 查看原因后重试"
fi

# ---------------------------------------------------------------- migrate
if [[ "${needs_migration}" == true ]]; then
  step "数据库迁移"
  run compose run --rm -T migrate </dev/null || fail "迁移失败，应用容器未替换，旧版本仍在运行"
fi

# ---------------------------------------------------------------- replace
check_background_tasks
step "替换应用容器"
run compose up -d --no-deps --force-recreate --wait --wait-timeout 240 "${app_services[@]}" \
  || fail "替换后有服务未在 240 秒内健康，请查看：docker compose -f ${compose_file} ps / logs"

# ---------------------------------------------------------------- verify
step "核对运行版本"
if [[ "${dry_run}" == true ]]; then
  info "[dry-run] 跳过版本核对"
  exit 0
fi
ok=true
if [[ " ${app_services[*]} " == *" backend "* ]]; then
  backend_commit="$(compose_exec backend python -m app.core.build_info | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])')"
  if [[ "${backend_commit}" == "${commit}" ]]; then info "后端 ✓ ${backend_commit:0:8}"; else info "后端 ✗ 运行 ${backend_commit:0:8}，期望 ${commit:0:8}"; ok=false; fi
fi
if [[ " ${app_services[*]} " == *" frontend "* ]]; then
  if compose_exec frontend sh -c "grep -q '${commit}' /usr/share/nginx/html/assets/*.js"; then
    info "前端 ✓ ${commit:0:8}"
  else
    info "前端 ✗ 静态资源中未找到 ${commit:0:8}"; ok=false
  fi
fi
compose ps --format 'table {{.Service}}\t{{.Status}}' "${app_services[@]}"
[[ "${ok}" == true ]] || fail "版本核对未通过"
step "部署完成：$(git log -1 --format='%h %s')（浏览器强制刷新后生效）"
