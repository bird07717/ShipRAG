#!/usr/bin/env bash
# Build only; production startup remains an explicit operator action.
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_root}"
export BUILD_COMMIT="$(git rev-parse HEAD)"
export BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export BUILD_DIRTY=false
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  export BUILD_DIRTY=true
fi
compose_args=()
if [[ "${1:-}" == "--env-file" ]]; then
  if [[ $# -lt 2 ]]; then
    printf 'Usage: bash scripts/build-production.sh [--env-file FILE] [build options/services]\n' >&2
    exit 2
  fi
  compose_args=(--env-file "$2")
  shift 2
fi
printf 'Building commit %s (dirty=%s), timestamp %s\n' "${BUILD_COMMIT}" "${BUILD_DIRTY}" "${BUILD_TIME}"
exec docker compose "${compose_args[@]}" --file compose.production.yaml build "$@"
