#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON_BIN:-python3}"

if [[ ! -d "${project_root}/.venv" ]]; then
  "${python_bin}" -m venv "${project_root}/.venv"
fi

"${project_root}/.venv/bin/python" -m pip install "pip==25.2"
"${project_root}/.venv/bin/python" -m pip install -r "${project_root}/backend/requirements.lock"
"${project_root}/.venv/bin/python" -m pip install --no-deps -e "${project_root}/backend"
npm --prefix "${project_root}/frontend" ci

if [[ ! -f "${project_root}/.env" ]]; then
  cp "${project_root}/.env.example" "${project_root}/.env"
fi

echo "Bootstrap complete. Review .env before starting services."
