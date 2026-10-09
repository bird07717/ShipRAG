"""Exercise deploy control flow with mocked Docker/Git; never touch containers."""

from __future__ import annotations

import fcntl
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
  printf 'git %s\n' "$*" >> "$TEST_LOG"
  case "$1" in
    status|fetch|merge) return 0 ;;
    merge-base) [[ "${TEST_NOT_FAST_FORWARD:-0}" != 1 ]] ;;
    rev-parse)
      [[ "$*" != *'@{upstream}'* || "${TEST_NO_UPSTREAM:-0}" != 1 ]] || return 1
      printf 'abcdef123456\n'
      ;;
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
        config)
          [[ "${TEST_CONFIG_STATUS:-0}" == 0 ]] || return 1
          printf '%s\n' "$TEST_CONFIG"
          ;;
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
            backend)
              [[ "${TEST_VERSION_STATUS:-0}" == 0 ]] || return 1
              printf '{"commit":"abcdef123456"}\n'
              ;;
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


def deploy(
    tmp_path: Path, *args: str, stdin_text: str | None = None, **overrides: str
) -> tuple[subprocess.CompletedProcess, str]:
    log = tmp_path / "commands.log"
    env = {
        **os.environ,
        "DEPLOY_ROOT": str(ROOT),
        "TEST_LOG": str(log),
        "DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
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
        input=stdin_text,
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


def position(log: str, needle: str) -> int:
    assert needle in log, f"{needle!r} not in log:\n{log}"
    return log.index(needle)


def test_checkout_only_moves_after_confirmation(tmp_path: Path) -> None:
    result, log = deploy(tmp_path, "--backend-only", "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert position(log, "git fetch") < position(log, "exec -T postgres")
    assert position(log, "exec -T postgres") < position(log, "git merge --ff-only abcdef123456")
    assert position(log, "git merge --ff-only abcdef123456") < position(log, "build scripts/")
    assert "git pull" not in log


def test_declining_confirmation_leaves_checkout_untouched(tmp_path: Path) -> None:
    result, log = deploy(tmp_path, "--backend-only", stdin_text="n\n")
    assert result.returncode != 0
    assert "已取消" in result.stderr
    assert "git fetch" in log
    assert "git merge --ff-only" not in log
    assert "docker tag " not in log
    assert "build scripts/" not in log


def test_dry_run_fetches_to_preview_but_never_merges(tmp_path: Path) -> None:
    result, log = deploy(tmp_path, "--backend-only", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "git fetch" in log
    assert "git merge --ff-only" not in log
    assert "[dry-run] git merge --ff-only abcdef123456" in result.stdout


@pytest.mark.parametrize(
    "failure, message",
    [
        ({"TEST_NO_UPSTREAM": "1"}, "没有上游分支"),
        ({"TEST_NOT_FAST_FORWARD": "1"}, "无法快进"),
    ],
)
def test_unusable_upstream_stops_before_any_change(
    tmp_path: Path, failure: dict[str, str], message: str
) -> None:
    result, log = deploy(tmp_path, "--backend-only", "--yes", **failure)
    assert result.returncode != 0
    assert message in result.stderr
    assert "git merge --ff-only" not in log
    assert "build scripts/" not in log


def test_rollback_accepts_yes_flag_right_after_it(tmp_path: Path) -> None:
    result, log = deploy(tmp_path, "--rollback", "-y")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"docker tag {BACKEND.rsplit(':', 1)[0]}:{OLD} {BACKEND}" in log
    assert ":-y" not in log


def tag_lines(log: str) -> list[str]:
    return [line for line in log.splitlines() if line.startswith("docker tag ")]


def test_backup_happens_only_after_build_migration_and_task_recheck(tmp_path: Path) -> None:
    result, log = deploy(tmp_path, "--skip-pull", "--backend-only", "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert position(log, "build scripts/") < position(log, "run --rm -T migrate")
    assert position(log, "run --rm -T migrate") < position(log, "docker tag ")
    assert position(log, "docker tag ") < position(log, "up -d --no-deps --force-recreate")
    # one task check before the build, one right before the replacement (and the backup)
    assert log.count("exec -T postgres") == 2


@pytest.mark.parametrize("failure", ["TEST_BUILD_STATUS", "TEST_MIGRATE_STATUS"])
def test_failed_build_or_migration_never_creates_or_prunes_backups(
    tmp_path: Path, failure: str
) -> None:
    """Repeated failed deploys must not push the last good backups out of retention."""
    tags = "\n".join(f"rollback-2026100{day}010000" for day in range(1, 5))
    result, log = deploy(
        tmp_path,
        "--skip-pull",
        "--backend-only",
        "--yes",
        TEST_BACKEND_TAGS=tags,
        **{failure: "1"},
    )
    assert result.returncode != 0
    assert "docker tag " not in log
    assert "docker rmi " not in log
    assert "up -d" not in log


def test_successful_backup_prunes_only_beyond_retention(tmp_path: Path) -> None:
    tags = "\n".join(f"rollback-2026100{day}010000" for day in range(1, 5))
    result, log = deploy(tmp_path, "--skip-pull", "--backend-only", "--yes", TEST_BACKEND_TAGS=tags)
    assert result.returncode == 0, result.stdout + result.stderr
    removed = [line for line in log.splitlines() if line.startswith("docker rmi ")]
    assert removed == [f"docker rmi {BACKEND.rsplit(':', 1)[0]}:rollback-20261001010000"]


@pytest.mark.parametrize(
    ("backend", "frontend"),
    [
        ("reg.example/app:backend", "reg.example/app:frontend"),
        ("reg.example:5000/app", "reg.example:5000/app:frontend"),
        ("reg.example/app@sha256:" + "a" * 64, "reg.example/app:frontend"),
    ],
)
def test_images_sharing_a_repository_are_rejected_before_any_change(
    tmp_path: Path, backend: str, frontend: str
) -> None:
    config = json.dumps(
        {"services": {"backend": {"image": backend}, "frontend": {"image": frontend}}}
    )
    result, log = deploy(tmp_path, "--skip-pull", "--yes", TEST_CONFIG=config)
    assert result.returncode != 0
    assert "同属仓库" in result.stderr
    assert "docker tag " not in log
    assert "build scripts/" not in log


@pytest.mark.parametrize(
    ("backend", "expected_repo"),
    [
        ("registry.example:5000/backend", "registry.example:5000/backend"),  # no tag, port kept
        ("registry.example:5000/backend:current", "registry.example:5000/backend"),
        ("registry.example/backend@sha256:" + "b" * 64, "registry.example/backend"),
    ],
)
def test_backup_tag_uses_the_repository_without_tag_or_digest(
    tmp_path: Path, backend: str, expected_repo: str
) -> None:
    config = json.dumps(
        {"services": {"backend": {"image": backend}, "frontend": {"image": FRONTEND}}}
    )
    result, log = deploy(
        tmp_path,
        "--skip-pull",
        "--backend-only",
        "--yes",
        TEST_CONFIG=config,
        TEST_BACKEND_TAGS="",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(
        line.startswith(f"docker tag sha256:live-backend-container {expected_repo}:rollback-")
        for line in tag_lines(log)
    ), log


def test_concurrent_deploy_is_refused_while_the_lock_is_held(tmp_path: Path) -> None:
    with (tmp_path / "deploy.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result, log = deploy(tmp_path, "--skip-pull", "--backend-only", "--yes")
    assert result.returncode != 0
    assert "另一个部署/回滚正在进行" in result.stderr
    assert "docker tag " not in log
    assert "build scripts/" not in log
    assert "up -d" not in log


def test_dry_run_does_not_take_the_lock(tmp_path: Path) -> None:
    with (tmp_path / "deploy.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result, _ = deploy(tmp_path, "--skip-pull", "--backend-only", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr


def test_first_deployment_skips_the_task_check_when_postgres_is_not_running(
    tmp_path: Path,
) -> None:
    result, log = deploy(tmp_path, "--skip-pull", "--backend-only", "--yes", TEST_NO_CONTAINER="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "postgres 未运行" in result.stdout
    assert "exec -T postgres" not in log
    assert "up -d --no-deps --force-recreate" in log


def test_unreadable_postgres_state_blocks_even_with_force(tmp_path: Path) -> None:
    result, log = deploy(
        tmp_path, "--skip-pull", "--backend-only", "--yes", "--force", TEST_PS_STATUS="1"
    )
    assert result.returncode != 0
    assert "up -d" not in log
    assert "build scripts/" not in log


def test_confirmation_without_input_explains_itself(tmp_path: Path) -> None:
    result, log = deploy(tmp_path, "--skip-pull", "--backend-only", stdin_text="")
    assert result.returncode != 0
    assert "无法读取确认输入" in result.stderr
    assert "docker tag " not in log


def test_unresolvable_compose_config_explains_itself(tmp_path: Path) -> None:
    result, log = deploy(tmp_path, "--skip-pull", "--backend-only", "--yes", TEST_CONFIG_STATUS="1")
    assert result.returncode != 0
    assert "无法解析 compose 镜像名" in result.stderr
    assert "build scripts/" not in log


def test_unreadable_running_version_fails_verification_with_a_message(tmp_path: Path) -> None:
    result, _ = deploy(tmp_path, "--skip-pull", "--backend-only", "--yes", TEST_VERSION_STATUS="1")
    assert result.returncode != 0
    assert "无法读取运行版本" in result.stdout
    assert "版本核对未通过" in result.stderr
