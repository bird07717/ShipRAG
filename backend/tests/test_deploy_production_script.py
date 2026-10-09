"""Exercise deploy control flow with mocked Docker/Git; never touch containers."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = "registry.example:5000/backend:current"
FRONTEND = "registry.example:5000/frontend:current"
OLD = "rollback-20261008010000"
NEW = "rollback-20261009010000"

MOCK_COMMANDS = r"""
git() {
  case "$1" in
    status|pull) return 0 ;;
    rev-parse) printf 'abcdef123456\n' ;;
    log) printf 'abcdef1 test build\n' ;;
    *) return 90 ;;
  esac
}
bash() {
  printf 'build %s\n' "$*" >> "$TEST_LOG"
  return "${TEST_BUILD_STATUS:-0}"
}
docker() {
  printf 'docker %s\n' "$*" >> "$TEST_LOG"
  case "$1" in
    compose)
      shift
      while [[ "$1" != '-f' ]]; do shift; done
      shift 2
      case "$1" in
        config) printf '%s\n' "$TEST_CONFIG" ;;
        ps)
          if [[ "${2:-}" == '--status' ]]; then
            [[ "${TEST_PS_STATUS:-0}" == 0 ]] || return 1
            if [[ "${TEST_NO_CONTAINER:-0}" != 1 ]]; then
              printf '%s-container\n' "${@: -1}"
              if [[ "${TEST_MIXED_IMAGES:-0}" == 1 ]]; then printf 'other-container\n'; fi
            fi
          fi
          ;;
        exec)
          case "$3" in
            postgres)
              [[ "${TEST_TASK_STATUS:-0}" == 0 ]] || return 1
              local count=0 counts
              [[ ! -f "$TEST_COUNT_FILE" ]] || read -r count < "$TEST_COUNT_FILE"
              read -ra counts <<< "$TEST_TASK_COUNTS"
              local index=$count
              if (( index >= ${#counts[@]} )); then index=$((${#counts[@]} - 1)); fi
              printf '%s\n' "${counts[$index]}"
              printf '%s\n' "$((count + 1))" > "$TEST_COUNT_FILE"
              ;;
            backend) printf '{"commit":"abcdef123456"}\n' ;;
            frontend) return 0 ;;
            *) return 90 ;;
          esac
          ;;
        run) return "${TEST_MIGRATE_STATUS:-0}" ;;
        up) return 0 ;;
        *) return 90 ;;
      esac
      ;;
    images)
      [[ "${TEST_LIST_STATUS:-0}" == 0 ]] || return 1
      case "${@: -1}" in
        *backend) printf '%s\n' "$TEST_BACKEND_TAGS" ;;
        *frontend) printf '%s\n' "$TEST_FRONTEND_TAGS" ;;
        *) return 90 ;;
      esac
      ;;
    image)
      [[ "$2" == inspect ]] || return 90
      [[ " $TEST_AVAILABLE " == *" $3 "* ]]
      ;;
    inspect)
      [[ "${TEST_INSPECT_STATUS:-0}" == 0 ]] || return 1
      if [[ "${@: -1}" == other-container ]]; then
        printf 'sha256:different\n'
      else
        printf 'sha256:live-%s\n' "${@: -1}"
      fi
      ;;
    tag|rmi) return 0 ;;
    buildx) return 0 ;;
    *) return 90 ;;
  esac
}
"""


def deploy(tmp_path: Path, *args: str, **overrides: str) -> tuple[subprocess.CompletedProcess, str]:
    log = tmp_path / "commands.log"
    env = {
        **os.environ,
        "DEPLOY_ROOT": str(ROOT),
        "TEST_LOG": str(log),
        "TEST_COUNT_FILE": str(tmp_path / "task-check-count"),
        "TEST_CONFIG": json.dumps(
            {"services": {"backend": {"image": BACKEND}, "frontend": {"image": FRONTEND}}}
        ),
        "TEST_TASK_COUNTS": "0",
        "TEST_BACKEND_TAGS": OLD,
        "TEST_FRONTEND_TAGS": OLD,
        "TEST_AVAILABLE": f"{BACKEND.rsplit(':', 1)[0]}:{OLD} {FRONTEND.rsplit(':', 1)[0]}:{OLD}",
        **overrides,
    }
    command = (
        MOCK_COMMANDS
        + "\nsource "
        + shlex.join([str(ROOT / "scripts/deploy-production.sh"), *args])
    )
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return result, log.read_text() if log.exists() else ""


def test_backup_uses_running_image_id_not_mutable_tag(tmp_path: Path) -> None:
    result, log = deploy(tmp_path, "--skip-pull", "--backend-only", "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "docker tag sha256:live-backend-container registry.example:5000/backend:rollback-" in log
    assert f"docker tag {BACKEND} " not in log
    assert log.count("exec -T postgres") == 2
    assert "run --rm -T migrate" in log
    assert "up -d --no-deps --force-recreate" in log


def test_first_deployment_skips_backup_without_running_container(tmp_path: Path) -> None:
    result, log = deploy(tmp_path, "--skip-pull", "--backend-only", "--yes", TEST_NO_CONTAINER="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "无运行容器，跳过备份" in result.stdout
    assert "docker tag " not in log


@pytest.mark.parametrize("failure", ["TEST_PS_STATUS", "TEST_INSPECT_STATUS", "TEST_MIXED_IMAGES"])
def test_backup_read_failure_or_mixed_replicas_stops_before_build(
    tmp_path: Path, failure: str
) -> None:
    result, log = deploy(tmp_path, "--skip-pull", "--backend-only", "--yes", **{failure: "1"})
    assert result.returncode != 0
    assert "build scripts/build-production.sh" not in log
    assert "docker tag " not in log
    assert "up -d" not in log


@pytest.mark.parametrize(
    ("target", "image", "empty_repo"),
    [
        ("--frontend-only", FRONTEND, "TEST_BACKEND_TAGS"),
        ("--backend-only", BACKEND, "TEST_FRONTEND_TAGS"),
    ],
)
def test_rollback_selects_backup_for_target_service(
    tmp_path: Path, target: str, image: str, empty_repo: str
) -> None:
    result, log = deploy(tmp_path, "--rollback", target, "--yes", **{empty_repo: ""})
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker tag {image.rsplit(':', 1)[0]}:{OLD} {image}" in log
    assert "build scripts/" not in log
    assert "run --rm" not in log
    assert log.count("exec -T postgres") == (0 if target == "--frontend-only" else 1)


def test_full_rollback_selects_latest_common_tag(tmp_path: Path) -> None:
    backend_repo = BACKEND.rsplit(":", 1)[0]
    frontend_repo = FRONTEND.rsplit(":", 1)[0]
    result, log = deploy(
        tmp_path,
        "--rollback",
        "--yes",
        TEST_BACKEND_TAGS=f"{OLD}\n{NEW}",
        TEST_AVAILABLE=f"{backend_repo}:{OLD} {frontend_repo}:{OLD} {backend_repo}:{NEW}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker tag {backend_repo}:{OLD} {BACKEND}" in log
    assert f"docker tag {frontend_repo}:{OLD} {FRONTEND}" in log
    assert f"docker tag {backend_repo}:{NEW}" not in log


@pytest.mark.parametrize("explicit_tag", [False, True])
def test_rollback_requires_all_target_images_before_mutation(
    tmp_path: Path, explicit_tag: bool
) -> None:
    args = ["--rollback", *([OLD] if explicit_tag else []), "--yes"]
    result, log = deploy(tmp_path, *args, TEST_AVAILABLE=f"{BACKEND.rsplit(':', 1)[0]}:{OLD}")
    assert result.returncode != 0
    assert "docker tag " not in log
    assert "up -d" not in log


@pytest.mark.parametrize(
    "overrides", [{"TEST_TASK_COUNTS": "3"}, {"TEST_TASK_STATUS": "1"}, {"TEST_TASK_COUNTS": "bad"}]
)
def test_rollback_task_guard_stops_before_retag_or_replacement(
    tmp_path: Path, overrides: dict[str, str]
) -> None:
    result, log = deploy(tmp_path, "--rollback", "--backend-only", "--yes", **overrides)
    assert result.returncode != 0
    assert "exec -T postgres" in log
    assert "docker tag " not in log
    assert "up -d" not in log


def test_force_allows_rollback_with_active_tasks(tmp_path: Path) -> None:
    result, log = deploy(
        tmp_path, "--rollback", "--backend-only", "--yes", "--force", TEST_TASK_COUNTS="3"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "进行中任务可能被中断" in result.stdout
    assert "up -d" in log


@pytest.mark.parametrize("counts", ["0 3", "0 bad"])
def test_task_starting_during_build_or_invalid_final_read_stops_replacement(
    tmp_path: Path, counts: str
) -> None:
    result, log = deploy(
        tmp_path, "--skip-pull", "--backend-only", "--yes", TEST_TASK_COUNTS=counts
    )
    assert result.returncode != 0
    assert "build scripts/build-production.sh" in log
    assert "run --rm -T migrate" in log
    assert log.count("exec -T postgres") == 2
    assert "up -d" not in log


def test_force_does_not_bypass_task_read_failure(tmp_path: Path) -> None:
    result, log = deploy(
        tmp_path, "--rollback", "--backend-only", "--yes", "--force", TEST_TASK_STATUS="1"
    )
    assert result.returncode != 0
    assert "docker tag " not in log
    assert "up -d" not in log


def test_frontend_deploy_never_queries_background_tasks(tmp_path: Path) -> None:
    result, log = deploy(tmp_path, "--skip-pull", "--frontend-only", "--yes", TEST_TASK_STATUS="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "exec -T postgres" not in log
    assert "run --rm" not in log


@pytest.mark.parametrize("rollback", [False, True])
def test_dry_run_never_executes_mutations(tmp_path: Path, rollback: bool) -> None:
    result, log = deploy(
        tmp_path,
        *(["--rollback"] if rollback else ["--skip-pull"]),
        "--backend-only",
        "--dry-run",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[dry-run]" in result.stdout
    assert "docker tag " not in log
    assert "docker rmi " not in log
    assert "build scripts/" not in log
    assert "run --rm" not in log
    assert "up -d" not in log


def test_failed_backup_listing_blocks_rollback(tmp_path: Path) -> None:
    result, log = deploy(tmp_path, "--rollback", "--yes", TEST_LIST_STATUS="1")
    assert result.returncode != 0
    assert "无法读取备份镜像列表" in result.stderr
    assert "docker tag " not in log


@pytest.mark.parametrize("failure", ["TEST_BUILD_STATUS", "TEST_MIGRATE_STATUS"])
def test_build_or_migration_failure_keeps_old_containers(tmp_path: Path, failure: str) -> None:
    result, log = deploy(tmp_path, "--skip-pull", "--backend-only", "--yes", **{failure: "1"})
    assert result.returncode != 0
    assert "up -d" not in log
